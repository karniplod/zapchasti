"""Заказы в бэкенде: список, карточка заказа, правка, посылки, лента.

Карточка устроена как заказ в CRM (Битрикс24, RetailCRM): сверху — этапы,
слева — данные блоками (покупатель, получение, состав, посылки, оплата),
справа — лента: что происходило с заказом и кто это сделал.

Покупатель ошибся в имени, телефоне или адресе — менеджер правит заказ
здесь же, каждая правка ложится в ленту «было → стало». Деньги (состав,
цены, доставку) можно менять, пока заказ не оплачен: после оплаты сумма
обязана совпадать с тем, что человек заплатил и что пробито в чеке.

Приложение сотрудника пользуется тем же: списком, PATCH статуса заказа
({"status": ...}) и посылки — их форма не меняется.
"""

import re
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import address as addr
from .. import customer_auth as ca
from .. import delivery as ship_services
from .. import order_log, payments
from ..auth import current_user, require_role
from ..database import get_session
from ..templating import templates
from .shop import ORDER_LABELS, PAYMENT_LABELS, PAYMENT_STATUS, SHIPMENT_LABELS, shipments_of

router = APIRouter(tags=["orders"])

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def money(v) -> str:
    return f"{Decimal(v or 0):,.0f}".replace(",", " ") + " ₽"


# ------------------------------------------------------------------
# Страницы
# ------------------------------------------------------------------


@router.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse("admin/orders.html", {"request": request, "user": user})


@router.get("/orders/{number}", response_class=HTMLResponse)
async def order_card_page(number: str, request: Request,
                          session: AsyncSession = Depends(get_session),
                          user=Depends(current_user)):
    oid = (await session.execute(text("SELECT id FROM orders WHERE number = :n"),
                                 {"n": number})).scalar()
    if not oid:
        raise HTTPException(404, "Заказ не найден")
    return templates.TemplateResponse("admin/order.html", {
        "request": request, "user": user, "order_id": oid, "number": number})


# ------------------------------------------------------------------
# Список
# ------------------------------------------------------------------


def _search(q: str) -> tuple[str, dict]:
    """Поиск как в CRM — одной строкой: номер, ФИО, телефон любой
    записью, артикул, номер отслеживания."""
    q = (q or "").strip()
    if not q:
        return "", {}
    digits = re.sub(r"\D", "", q)
    # Телефон ищем по последним цифрам: +7 и 8 в начале пишут по-разному
    phone = digits[-10:] if len(digits) >= 5 else None
    return """
           AND (o.number ILIKE :q OR o.contact_name ILIKE :q OR c.name ILIKE :q
                OR o.contact_email ILIKE :q OR c.email ILIKE :q
                OR o.delivery_address ILIKE :q
                OR (CAST(:ph AS text) IS NOT NULL AND
                    (regexp_replace(coalesce(o.contact_phone, ''), '\\D', '', 'g') LIKE :phl
                     OR regexp_replace(coalesce(c.phone, ''), '\\D', '', 'g') LIKE :phl))
                OR EXISTS (SELECT 1 FROM order_items oi JOIN parts p ON p.id = oi.part_id
                            WHERE oi.order_id = o.id AND (p.sku ILIKE :q OR p.name ILIKE :q))
                OR EXISTS (SELECT 1 FROM order_shipments s
                            WHERE s.order_id = o.id AND s.track_number ILIKE :q))""", {
        "q": f"%{q}%", "ph": phone, "phl": f"%{phone}%" if phone else None}


@router.get("/api/manage/orders")
async def orders_list(
    status: str | None = None,
    mine: bool = False,
    q: str = "",
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    """mine — заказы, где есть работа у филиала сотрудника: посылка из
    его филиала или самовывоз из него. q — поиск одной строкой."""
    if mine and not user.get("branch_id"):
        raise HTTPException(422, "У вашей учётной записи не указан филиал")
    where, params = _search(q)
    rows = await session.execute(
        text(f"""
        SELECT o.id, o.number, o.status::text AS status, o.total, o.created_at,
               o.paid_at, o.delivery_method, o.delivery_address, o.comment,
               -- Получатель из формы заказа важнее карточки покупателя:
               -- заказывать мог один человек, а забирать — другой
               coalesce(o.contact_phone, c.phone) AS phone,
               coalesce(o.contact_name, c.name) AS customer_name,
               o.payment_method, o.delivery_carrier, o.delivery_mode, o.delivery_price,
               o.delivery_postcode, o.delivery_city, o.manager_note,
               (SELECT br.city || ', ' || br.name FROM branches br
                 WHERE br.id = o.pickup_branch_id) AS pickup_branch,
               (SELECT count(*) FROM order_items oi WHERE oi.order_id = o.id) AS items
          FROM orders o
          LEFT JOIN customers c ON c.id = o.customer_id
         WHERE (CAST(:st AS text) IS NULL OR o.status::text = CAST(:st AS text))
           AND (CAST(:br AS int) IS NULL
                OR o.pickup_branch_id = CAST(:br AS int)
                OR EXISTS (SELECT 1 FROM order_shipments s
                            WHERE s.order_id = o.id AND s.branch_id = CAST(:br AS int)))
           {where}
         ORDER BY o.created_at DESC
         LIMIT 200
    """),
        {"st": status, "br": user["branch_id"] if mine else None, **params},
    )
    orders = [dict(r._mapping) for r in rows]
    if not orders:
        return []

    items = await session.execute(
        text("""
        SELECT oi.order_id, oi.price, oi.qty, oi.shipment_id,
               p.sku, p.name, p.status::text AS status,
               (SELECT br.city || ', ' || br.name FROM branches br
                 WHERE br.id = p.branch_id) AS branch
          FROM order_items oi
          JOIN parts p ON p.id = oi.part_id
         WHERE oi.order_id = ANY(:ids)
         ORDER BY oi.id
    """),
        {"ids": [o["id"] for o in orders]},
    )
    by_order: dict[int, list] = {}
    for r in items:
        by_order.setdefault(r.order_id, []).append(dict(r._mapping))
    ships: dict[int, list] = {}
    for r in await shipments_of(session, [o["id"] for o in orders]):
        ships.setdefault(r["order_id"], []).append(r)
    for o in orders:
        o["items"] = by_order.get(o["id"], [])
        o["shipments"] = ships.get(o["id"], [])
    return orders


@router.get("/api/manage/orders/counts")
async def orders_counts(mine: bool = False, session: AsyncSession = Depends(get_session),
                        user=Depends(current_user)):
    """Сколько заказов в каждом статусе — для вкладок списка."""
    rows = await session.execute(text("""
        SELECT o.status::text AS status, count(*) AS n FROM orders o
         WHERE CAST(:br AS int) IS NULL OR o.pickup_branch_id = CAST(:br AS int)
            OR EXISTS (SELECT 1 FROM order_shipments s
                        WHERE s.order_id = o.id AND s.branch_id = CAST(:br AS int))
         GROUP BY 1"""), {"br": user.get("branch_id") if mine else None})
    out = {r.status: r.n for r in rows}
    out[""] = sum(out.values())
    return out


# ------------------------------------------------------------------
# Карточка
# ------------------------------------------------------------------


async def cdek_code_of(city: str | None, code: int | None) -> int | None:
    """Код города у СДЭК: у заказов до карточки он не сохранялся —
    находим по названию, как подсказка города в корзине."""
    if code or not city:
        return code
    hit = await ship_services.cities(city)
    return hit[0]["cdek_code"] if hit else None


async def _order(session: AsyncSession, order_id: int):
    row = (await session.execute(text("""
        SELECT o.*, o.status::text AS status FROM orders o WHERE o.id = :id"""),
        {"id": order_id})).first()
    if not row:
        raise HTTPException(404, "Заказ не найден")
    return row


def money_locked(o) -> str | None:
    """Почему сумму заказа уже нельзя менять — или None, если можно."""
    if o.status == "cancelled":
        return "Заказ отменён"
    if o.paid_at or o.status not in ("new", "confirmed"):
        return "Заказ оплачен или отправлен — сумма должна совпадать с оплатой и чеком"
    return None


def edit_locked(o) -> str | None:
    if o.status in ("cancelled", "completed"):
        return "Заказ " + ORDER_LABELS.get(o.status, o.status) + " — данные не меняются"
    return None


@router.get("/api/manage/orders/{order_id}")
async def order_card(order_id: int, session: AsyncSession = Depends(get_session),
                     user=Depends(current_user)):
    o = await _order(session, order_id)
    order = dict(o._mapping)
    order["label"] = ORDER_LABELS.get(o.status, o.status)
    order["payment_label"] = PAYMENT_LABELS.get(o.payment_method or "")
    order["pickup_branch"] = (await session.execute(text("""
        SELECT city || ', ' || name FROM branches WHERE id = :b"""),
        {"b": o.pickup_branch_id})).scalar() if o.pickup_branch_id else None

    customer = None
    if o.customer_id:
        c = (await session.execute(text("""
            SELECT c.id, c.name, c.phone, c.email, c.created_at,
                   (SELECT count(*) FROM orders x WHERE x.customer_id = c.id) AS orders,
                   (SELECT coalesce(sum(total), 0) FROM orders x WHERE x.customer_id = c.id
                       AND x.status IN ('paid', 'shipped', 'completed')) AS spent
              FROM customers c WHERE c.id = :c"""), {"c": o.customer_id})).first()
        customer = dict(c._mapping) if c else None

    items = [dict(r._mapping) for r in await session.execute(text("""
        SELECT oi.id, oi.part_id, oi.price, oi.qty, oi.price * oi.qty AS sum, oi.shipment_id,
               p.sku, p.name, p.status::text AS status, p.condition::text AS condition,
               p.quantity AS stock, p.price AS price_now, p.location, p.branch_id,
               (SELECT br.city || ', ' || br.name FROM branches br
                 WHERE br.id = p.branch_id) AS branch,
               (SELECT coalesce(ph.thumb, ph.path) FROM part_photos ph
                 WHERE ph.part_id = p.id ORDER BY sort_order LIMIT 1) AS photo
          FROM order_items oi JOIN parts p ON p.id = oi.part_id
         WHERE oi.order_id = :o ORDER BY oi.id"""), {"o": order_id})]

    ships = await shipments_of(session, [order_id])
    for s in ships:
        s["label"] = SHIPMENT_LABELS.get(s["status"], s["status"])

    pays = [{**dict(r._mapping), "method_label": payments.title_of(r.method),
             "status_label": PAYMENT_STATUS.get(r.status, r.status)}
            for r in await session.execute(text("""
                SELECT method, provider, amount, status, created_at, paid_at
                  FROM payments WHERE order_id = :o ORDER BY id DESC"""), {"o": order_id})]

    events = [dict(r._mapping) for r in await session.execute(text("""
        SELECT e.id, e.kind, e.text, e.data, e.created_at,
               coalesce(u.full_name, u.login) AS who
          FROM order_events e LEFT JOIN users u ON u.id = e.user_id
         WHERE e.order_id = :o ORDER BY e.created_at DESC, e.id DESC"""), {"o": order_id})]

    return {
        "order": order, "customer": customer, "items": items, "shipments": ships,
        "payments": pays, "events": events,
        "goods": sum((i["sum"] for i in items), Decimal(0)),
        "money_locked": money_locked(o), "edit_locked": edit_locked(o),
        "can_edit": user["role"] in ("manager", "admin"),
    }


# ------------------------------------------------------------------
# Правка заказа
# ------------------------------------------------------------------


class OrderPatch(BaseModel):
    # Приложение сотрудника шлёт только status — остальное необязательно
    status: str | None = None
    contact_name: str | None = Field(default=None, max_length=120)
    contact_phone: str | None = Field(default=None, max_length=32)
    contact_email: str | None = Field(default=None, max_length=254)
    comment: str | None = Field(default=None, max_length=1000)
    manager_note: str | None = Field(default=None, max_length=4000)
    # Получение
    delivery_method: str | None = None
    pickup_branch_id: int | None = None
    delivery_city: str | None = Field(default=None, max_length=120)
    delivery_cdek_code: int | None = None
    delivery_postcode: str | None = Field(default=None, max_length=6)
    delivery_country: str | None = Field(default=None, max_length=2)
    delivery_street: str | None = Field(default=None, max_length=120)
    delivery_house: str | None = Field(default=None, max_length=20)
    delivery_block: str | None = Field(default=None, max_length=20)
    delivery_flat: str | None = Field(default=None, max_length=20)
    delivery_point: str | None = Field(default=None, max_length=64)
    # Доставка без посылок (самовывоз, «Доставка ТК») — цену ставит менеджер
    delivery_price: Decimal | None = Field(default=None, ge=0, le=1_000_000)


# Куда можно перевести заказ. Список, а не свободный переход: «отменён»
# возвращает детали на витрину, и случайный клик из «выдан» в «новый»
# выложил бы проданное обратно
ORDER_FLOW = {"new", "confirmed", "paid", "shipped", "completed", "cancelled"}

FIELD_LABELS = {"contact_name": "Получатель", "contact_phone": "Телефон",
                "contact_email": "Email", "comment": "Комментарий покупателя",
                "manager_note": "Заметка для сотрудников"}
ADDRESS_FIELDS = ("delivery_city", "delivery_cdek_code", "delivery_postcode", "delivery_country",
                  "delivery_street", "delivery_house", "delivery_block", "delivery_flat",
                  "delivery_point")


async def shipping_address(session: AsyncSession, o, payload, sent: set) -> tuple[dict, list, bool]:
    """Новый адрес доставки из правки — менеджера или покупателя: проверка
    полей, пункт выдачи из списка службы, адрес одной строкой. Ещё не
    отправленные посылки едут по новому адресу.
    → (поля заказа для записи, «было → стало» для ленты, нужен ли пересчёт)."""
    upd: dict = {}
    changes: list[str] = []
    city = " ".join((payload.delivery_city if "delivery_city" in sent
                     else o.delivery_city or "").split())
    if len(city) < 2:
        raise HTTPException(422, "Укажите город доставки")
    post = payload.delivery_postcode if "delivery_postcode" in sent else o.delivery_postcode
    post = (post or "").strip() or None
    if post and not re.fullmatch(r"\d{6}", post):
        raise HTTPException(422, "Индекс — шесть цифр")
    if o.delivery_mode == "post" and not post:
        raise HTTPException(422, "Для Почты России нужен индекс")
    code = payload.delivery_cdek_code if "delivery_cdek_code" in sent else o.delivery_cdek_code
    if city != (o.delivery_city or "") and "delivery_cdek_code" not in sent:
        code = None              # город другой — прежний код СДЭК к нему не относится
    upd.update(delivery_method="shipping", delivery_city=city, delivery_postcode=post,
               delivery_cdek_code=code, pickup_branch_id=None)
    if o.delivery_mode == "pvz":
        point = payload.delivery_point if "delivery_point" in sent else o.delivery_point
        if not point:
            raise HTTPException(422, "Выберите пункт выдачи")
        if point != o.delivery_point or city != (o.delivery_city or ""):
            code = await cdek_code_of(city, code)
            upd["delivery_cdek_code"] = code
            pts = await ship_services.points(o.delivery_carrier, {"city": city, "cdek_code": code})
            hit = next((p for p in pts if p["code"] == point), None)
            if not hit:
                raise HTTPException(422, "Пункт выдачи не найден — выберите из списка")
            full = f"{hit['address']} (пункт {hit['code']})"
            upd.update(delivery_point=point, delivery_point_address=full, delivery_address=full)
    else:
        vals = {f: (getattr(payload, f) if f in sent else getattr(o, f))
                for f in ("delivery_country", "delivery_street", "delivery_house",
                          "delivery_block", "delivery_flat")}
        if vals["delivery_street"] or vals["delivery_house"]:
            full, err = addr.compose(vals["delivery_country"] or "RU", post, city,
                                     vals["delivery_street"] or "", vals["delivery_house"] or "",
                                     vals["delivery_block"], vals["delivery_flat"])
            if err:
                raise HTTPException(422, err)
            upd.update({f: (v or "").strip() or None for f, v in vals.items()},
                       delivery_country=vals["delivery_country"] or "RU", delivery_address=full)
        elif o.delivery_method != "shipping" or not o.delivery_address:
            raise HTTPException(422, "Укажите улицу и дом")
    if o.delivery_method == "pickup":
        changes.append("Получение: самовывоз → доставка")
    new_addr = upd.get("delivery_address", o.delivery_address)
    if (new_addr or None) != (o.delivery_address or None):
        changes.append(f"Адрес: {o.delivery_address or '—'} → {new_addr}")
    elif city != (o.delivery_city or ""):
        changes.append(f"Город: {o.delivery_city or '—'} → {city}")
    if post != o.delivery_postcode and "Адрес:" not in " ".join(changes):
        changes.append(f"Индекс: {o.delivery_postcode or '—'} → {post or '—'}")
    # Куда везти поменялось — у службы другая цена. Пересчёт — отдельной
    # кнопкой: менеджер видит новую цену до того, как она войдёт в сумму
    need_recalc = bool(o.delivery_carrier) and (
        city != (o.delivery_city or "") or post != o.delivery_postcode
        or upd.get("delivery_point", o.delivery_point) != o.delivery_point)
    # Ещё не отправленные посылки едут по новому адресу
    await session.execute(text("""
        UPDATE order_shipments SET address = :a, point = :p
         WHERE order_id = :o AND status = 'assembling'"""),
        {"a": upd.get("delivery_address", o.delivery_address),
         "p": upd.get("delivery_point", o.delivery_point), "o": o.id})
    return upd, changes, need_recalc


@router.patch("/api/manage/orders/{order_id}")
async def patch_order(
    order_id: int,
    payload: OrderPatch,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("manager")),
):
    o = await _order(session, order_id)
    sent = payload.model_fields_set
    changes: list[str] = []
    need_recalc = False

    # --- контакты и комментарии -------------------------------------
    upd: dict = {}
    if sent & {"contact_name", "contact_phone", "contact_email", "comment", "delivery_method",
               "pickup_branch_id", "delivery_price", *ADDRESS_FIELDS}:
        if why := edit_locked(o):
            raise HTTPException(409, why)
    if "contact_name" in sent:
        name = " ".join((payload.contact_name or "").split())
        if not ca.NAME_RE.match(name):
            raise HTTPException(422, "Имя получателя — только буквы, пробел и дефис")
        upd["contact_name"] = name
    if "contact_phone" in sent:
        phone = ca.normalize_phone(payload.contact_phone or "")
        if not phone:
            raise HTTPException(422, "Телефон в формате +7 900 000-00-00")
        upd["contact_phone"] = phone
    if "contact_email" in sent:
        email = (payload.contact_email or "").strip().lower() or None
        if email and not EMAIL_RE.match(email):
            raise HTTPException(422, "Email в формате name@example.ru")
        upd["contact_email"] = email
    for f in ("comment", "manager_note"):
        if f in sent:
            upd[f] = (getattr(payload, f) or "").strip() or None
    for f, v in upd.items():
        old = getattr(o, f)
        if (old or None) != v:
            changes.append(f"{FIELD_LABELS[f]}: {old or '—'} → {v or '—'}")

    # --- получение ------------------------------------------------------
    method = payload.delivery_method if "delivery_method" in sent else o.delivery_method
    if method not in ("pickup", "shipping"):
        raise HTTPException(422, "Способ получения: самовывоз или доставка")
    ships = (await session.execute(text("""
        SELECT id, status FROM order_shipments WHERE order_id = :o AND status <> 'cancelled'"""),
        {"o": order_id})).all()
    money_why = money_locked(o)

    if method == "pickup" and ({"delivery_method", "pickup_branch_id"} & sent):
        branch = (await session.execute(text("""
            SELECT id, city || ', ' || name AS title FROM branches WHERE id = :b AND is_active"""),
            {"b": payload.pickup_branch_id or o.pickup_branch_id})).first()
        if not branch:
            raise HTTPException(422, "Выберите филиал для самовывоза")
        if o.delivery_method != "pickup":
            # Передумал везти — заберёт сам: посылки не нужны, доставка
            # из суммы уходит. Это деньги — только до оплаты
            if money_why and (o.delivery_price or 0):
                raise HTTPException(409, money_why)
            if any(s.status != "assembling" for s in ships):
                raise HTTPException(409, "Посылка уже отправлена — на самовывоз не перевести")
            await session.execute(text(
                "UPDATE order_items SET shipment_id = NULL WHERE order_id = :o"), {"o": order_id})
            await session.execute(text("DELETE FROM order_shipments WHERE order_id = :o"),
                                  {"o": order_id})
            upd.update(delivery_method="pickup", delivery_carrier=None, delivery_mode=None,
                       delivery_tariff=None, delivery_price=None, delivery_days=None,
                       delivery_point=None, delivery_point_address=None, delivery_address=None)
            changes.append(f"Получение: доставка → самовывоз ({branch.title})")
        elif branch.id != o.pickup_branch_id:
            old = (await session.execute(text("SELECT city || ', ' || name FROM branches WHERE id = :b"),
                                         {"b": o.pickup_branch_id})).scalar()
            changes.append(f"Самовывоз: {old or '—'} → {branch.title}")
        upd["pickup_branch_id"] = branch.id

    if method == "shipping" and (sent & {"delivery_method", *ADDRESS_FIELDS}):
        a_upd, a_changes, need_recalc = await shipping_address(session, o, payload, sent)
        upd.update(a_upd)
        changes += a_changes

    if "delivery_price" in sent:
        if ships:
            raise HTTPException(422, "Цена доставки складывается из посылок — меняйте её у посылки")
        if money_why:
            raise HTTPException(409, money_why)
        upd["delivery_price"] = payload.delivery_price or None
        if (o.delivery_price or 0) != (payload.delivery_price or 0):
            changes.append(f"Доставка: {money(o.delivery_price)} → {money(payload.delivery_price)}")

    if upd:
        sets = ", ".join(f"{k} = :{k}" for k in upd)
        await session.execute(text(f"UPDATE orders SET {sets}, updated_at = now() WHERE id = :id"),
                              {**upd, "id": order_id})
        await sync_totals(session, order_id)
    if changes:
        await order_log.log(session, order_id, "edit", "; ".join(changes), user)

    # --- статус ---------------------------------------------------------
    if "status" in sent and payload.status != o.status:
        if payload.status not in ORDER_FLOW:
            raise HTTPException(422, "Неизвестный статус")
        # Отмена вернула штуки на склад — их уже могли купить другие.
        # Оживлять такой заказ нельзя: оформляется новый
        if o.status == "cancelled":
            raise HTTPException(409, "Отменённый заказ не восстановить — оформите новый")
        await set_order_status(session, order_id, payload.status)
        await order_log.log(session, order_id, "status",
                            f"Статус: {ORDER_LABELS.get(o.status)} → {ORDER_LABELS.get(payload.status)}",
                            user)

    await session.commit()
    return {"ok": True, "need_recalc": need_recalc}


async def sync_totals(session: AsyncSession, order_id: int) -> None:
    """Сумма заказа = товары + доставка. Есть посылки — доставка равна
    сумме их цен: у заказа нет своей цены доставки отдельно от посылок."""
    await session.execute(text("""
        UPDATE orders o
           SET delivery_price = CASE
                 WHEN EXISTS (SELECT 1 FROM order_shipments s
                               WHERE s.order_id = o.id AND s.status <> 'cancelled')
                 THEN (SELECT sum(s.price) FROM order_shipments s
                        WHERE s.order_id = o.id AND s.status <> 'cancelled')
                 ELSE o.delivery_price END
         WHERE o.id = :o"""), {"o": order_id})
    await session.execute(text("""
        UPDATE orders o
           SET total = (SELECT coalesce(sum(price * qty), 0) FROM order_items WHERE order_id = o.id)
                       + coalesce(o.delivery_price, 0),
               updated_at = now()
         WHERE o.id = :o"""), {"o": order_id})


async def set_order_status(session: AsyncSession, order_id: int, status: str) -> None:
    """Новый статус и всё, что из него следует для деталей и посылок.
    Без commit — его делает вызывающий."""
    await session.execute(
        text("""
        UPDATE orders
           SET status = CAST(:st AS order_status), updated_at = now(),
               paid_at = CASE WHEN :st IN ('paid', 'shipped', 'completed')
                              THEN coalesce(paid_at, now()) ELSE paid_at END
         WHERE id = :id
    """),
        {"st": status, "id": order_id},
    )

    lines = (await session.execute(
        text("SELECT part_id, qty FROM order_items WHERE order_id = :id"), {"id": order_id}
    )).all()
    parts = [r.part_id for r in lines]

    if status == "cancelled":
        # Посылки заказа больше не собирают и не везут
        await session.execute(text("""
            UPDATE order_shipments SET status = 'cancelled'
             WHERE order_id = :id AND status <> 'delivered'"""), {"id": order_id})
        # Штуки возвращаются на склад, деталь — на витрину. Только если
        # её не продали и не списали руками: тогда возвращать некуда
        for r in lines:
            await return_stock(session, r.part_id, r.qty)
    elif status in ("paid", "shipped", "completed"):
        # Проданной считается деталь, у которой не осталось штук; если
        # остаток есть, она продолжает продаваться
        await session.execute(
            text("""
            UPDATE parts SET status = 'sold', updated_at = now()
             WHERE id = ANY(:ids) AND status = 'reserved' AND quantity = 0
        """),
            {"ids": parts},
        )


async def return_stock(session: AsyncSession, part_id: int, qty: int) -> None:
    await session.execute(text("""
        UPDATE parts SET quantity = quantity + :q,
               status = CASE WHEN status = 'reserved' THEN 'in_stock'::part_status ELSE status END,
               updated_at = now()
         WHERE id = :p AND status IN ('reserved', 'in_stock')"""), {"q": qty, "p": part_id})


async def take_stock(session: AsyncSession, part_id: int, qty: int) -> bool:
    """Списать штуки под заказ — как при оформлении: условие на остаток
    защищает от гонки, кончились — деталь уходит с витрины."""
    return bool((await session.execute(text("""
        UPDATE parts SET quantity = quantity - :q,
               status = CASE WHEN quantity - :q = 0 THEN 'reserved'::part_status ELSE status END,
               updated_at = now()
         WHERE id = :p AND status = 'in_stock' AND quantity >= :q"""),
        {"q": qty, "p": part_id})).rowcount)


# ------------------------------------------------------------------
# Состав заказа
# ------------------------------------------------------------------
# Покупатель передумал брать одну деталь, хочет две штуки вместо одной,
# договорились о скидке — менеджер правит состав, пока заказ не оплачен.
# Склад следует за составом: убрали — штуки вернулись, добавили — списались.


class ItemAdd(BaseModel):
    sku: str = Field(min_length=3, max_length=40)
    qty: int = Field(default=1, ge=1, le=999)


class ItemPatch(BaseModel):
    qty: int | None = Field(default=None, ge=1, le=999)
    price: Decimal | None = Field(default=None, ge=0, le=100_000_000)


async def _money_order(session, order_id):
    o = await _order(session, order_id)
    if why := money_locked(o):
        raise HTTPException(409, why)
    return o


@router.post("/api/manage/orders/{order_id}/items")
async def add_item(order_id: int, payload: ItemAdd, session: AsyncSession = Depends(get_session),
                   user=Depends(require_role("manager"))):
    o = await _money_order(session, order_id)
    p = (await session.execute(text("""
        SELECT id, sku, name, price, status::text AS status, quantity, branch_id
          FROM parts WHERE upper(sku) = upper(:s)"""), {"s": payload.sku.strip()})).first()
    if not p:
        raise HTTPException(404, "Деталь с таким артикулом не найдена")
    if p.status != "in_stock" or p.price is None:
        raise HTTPException(409, f"{p.sku} не продаётся: нет в наличии или нет цены")
    if not await take_stock(session, p.id, payload.qty):
        raise HTTPException(409, f"{p.sku}: в наличии только {p.quantity} шт.")

    have = (await session.execute(text("""
        SELECT id, qty FROM order_items WHERE order_id = :o AND part_id = :p"""),
        {"o": order_id, "p": p.id})).first()
    if have:
        await session.execute(text("UPDATE order_items SET qty = qty + :q WHERE id = :id"),
                              {"q": payload.qty, "id": have.id})
    else:
        sid = await _shipment_for(session, o, p.branch_id) if o.delivery_carrier else None
        await session.execute(text("""
            INSERT INTO order_items (order_id, part_id, price, qty, shipment_id)
            VALUES (:o, :p, :pr, :q, :s)"""),
            {"o": order_id, "p": p.id, "pr": p.price, "q": payload.qty, "s": sid})
    await sync_totals(session, order_id)
    await order_log.log(session, order_id, "item",
                        f"Добавлено: {p.sku} {p.name}, {payload.qty} шт × {money(p.price)}", user)
    await session.commit()
    return {"ok": True, "need_recalc": bool(o.delivery_carrier)}


async def _shipment_for(session, o, branch_id: int | None) -> int | None:
    """Посылка филиала детали: есть несобранная — в неё, нет — новая,
    с нулевой ценой: её цену даст пересчёт доставки."""
    sid = (await session.execute(text("""
        SELECT id FROM order_shipments WHERE order_id = :o AND branch_id IS NOT DISTINCT FROM :b
           AND status = 'assembling' ORDER BY id LIMIT 1"""),
        {"o": o.id, "b": branch_id})).scalar()
    if sid:
        return sid
    return (await session.execute(text("""
        INSERT INTO order_shipments (order_id, branch_id, carrier, mode, price, point, address)
        VALUES (:o, :b, :c, :m, 0, :pt, :a) RETURNING id"""),
        {"o": o.id, "b": branch_id, "c": o.delivery_carrier, "m": o.delivery_mode,
         "pt": o.delivery_point, "a": o.delivery_address})).scalar_one()


@router.patch("/api/manage/orders/{order_id}/items/{item_id}")
async def patch_item(order_id: int, item_id: int, payload: ItemPatch,
                     session: AsyncSession = Depends(get_session),
                     user=Depends(require_role("manager"))):
    await _money_order(session, order_id)
    it = (await session.execute(text("""
        SELECT oi.id, oi.part_id, oi.qty, oi.price, p.sku, p.quantity
          FROM order_items oi JOIN parts p ON p.id = oi.part_id
         WHERE oi.id = :i AND oi.order_id = :o"""), {"i": item_id, "o": order_id})).first()
    if not it:
        raise HTTPException(404, "Строки нет в заказе")
    changes = []
    if payload.qty is not None and payload.qty != it.qty:
        diff = payload.qty - it.qty
        if diff > 0 and not await take_stock(session, it.part_id, diff):
            raise HTTPException(409, f"{it.sku}: на складе ещё только {it.quantity} шт.")
        if diff < 0:
            await return_stock(session, it.part_id, -diff)
        changes.append(f"{it.qty} → {payload.qty} шт")
    if payload.price is not None and payload.price != it.price:
        changes.append(f"цена {money(it.price)} → {money(payload.price)}")
    if not changes:
        return {"ok": True}
    await session.execute(text("""
        UPDATE order_items SET qty = coalesce(:q, qty), price = coalesce(:p, price) WHERE id = :i"""),
        {"q": payload.qty, "p": payload.price, "i": item_id})
    await sync_totals(session, order_id)
    await order_log.log(session, order_id, "item", f"{it.sku}: " + ", ".join(changes), user)
    await session.commit()
    return {"ok": True}


@router.delete("/api/manage/orders/{order_id}/items/{item_id}")
async def delete_item(order_id: int, item_id: int, session: AsyncSession = Depends(get_session),
                      user=Depends(require_role("manager"))):
    await _money_order(session, order_id)
    it = (await session.execute(text("""
        SELECT oi.id, oi.part_id, oi.qty, oi.shipment_id, p.sku, p.name
          FROM order_items oi JOIN parts p ON p.id = oi.part_id
         WHERE oi.id = :i AND oi.order_id = :o"""), {"i": item_id, "o": order_id})).first()
    if not it:
        raise HTTPException(404, "Строки нет в заказе")
    left = (await session.execute(text("SELECT count(*) FROM order_items WHERE order_id = :o"),
                                  {"o": order_id})).scalar()
    if left <= 1:
        raise HTTPException(409, "Это последняя деталь — отмените заказ целиком")
    await return_stock(session, it.part_id, it.qty)
    await session.execute(text("DELETE FROM order_items WHERE id = :i"), {"i": item_id})
    # Посылка опустела — её больше нет, и её доставка уходит из суммы
    gone = False
    if it.shipment_id:
        gone = bool((await session.execute(text("""
            DELETE FROM order_shipments s WHERE s.id = :s
               AND NOT EXISTS (SELECT 1 FROM order_items WHERE shipment_id = s.id)"""),
            {"s": it.shipment_id})).rowcount)
    await sync_totals(session, order_id)
    await order_log.log(session, order_id, "item",
                        f"Убрано: {it.sku} {it.name}, {it.qty} шт"
                        + (" — посылка из этого филиала больше не нужна" if gone else ""), user)
    await session.commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Пересчёт доставки
# ------------------------------------------------------------------


class Recalc(BaseModel):
    apply: bool = False


async def quote_order(session: AsyncSession, o, why_suffix: str = "") -> tuple[dict, list, int | None]:
    """Цена доставки заказа той же службой и тем же способом — по составу
    и адресу, какие они сейчас в базе (в том числе ещё не записанные в
    этой транзакции). → (вариант службы, посылки, код города СДЭК).
    Служба не везёт — 409 с причиной."""
    ships = (await session.execute(text("""
        SELECT s.id, s.price, b.id AS bid, b.city, b.name, b.postcode, b.cdek_city_code,
               b.yandex_station_id
          FROM order_shipments s JOIN branches b ON b.id = s.branch_id
         WHERE s.order_id = :o AND s.status <> 'cancelled' ORDER BY s.id"""),
        {"o": o.id})).all()
    if not ships:
        raise HTTPException(422, "У заказа нет посылок")
    items = (await session.execute(text("""
        SELECT oi.shipment_id, oi.part_id, oi.qty, oi.price, p.size_class, p.weight_kg
          FROM order_items oi JOIN parts p ON p.id = oi.part_id WHERE oi.order_id = :o"""),
        {"o": o.id})).all()
    labels = ship_services.parcel_labels([{"city": s.city, "name": s.name} for s in ships])
    parcels = []
    for s, label in zip(ships, labels):
        lines = [dict(i._mapping) for i in items if i.shipment_id == s.id]
        parcels.append({
            "origin": {"id": s.bid, "city": s.city, "name": s.name, "postcode": s.postcode,
                       "cdek_city_code": s.cdek_city_code, "yandex_station_id": s.yandex_station_id,
                       "label": label},
            "items": lines, "value": sum(i["price"] * i["qty"] for i in lines)})
    code = await cdek_code_of(o.delivery_city, o.delivery_cdek_code)
    got = await ship_services.quotes(parcels, {
        "city": o.delivery_city, "cdek_code": code, "postcode": o.delivery_postcode,
        "point": o.delivery_point if o.delivery_carrier == "yandex" else None})
    opt = next((x for x in got["options"]
                if x["carrier"] == o.delivery_carrier and x["mode"] == o.delivery_mode), None)
    if not opt:
        raise HTTPException(409, (got["issues"].get(o.delivery_carrier) or
                                  "Служба не посчитала доставку по этому адресу") + why_suffix)
    return opt, ships, code


async def apply_quote(session: AsyncSession, order_id: int, opt: dict, ships: list,
                      code: int | None) -> None:
    """Записать посчитанную доставку в посылки и заказ, пересчитать сумму."""
    for s, x in zip(ships, opt["parcels"]):
        await session.execute(text("""
            UPDATE order_shipments SET price = :p, tariff = :t, days_min = :a, days_max = :b,
                   weight_g = :w WHERE id = :id"""),
            {"p": x["price"], "t": x["tariff"], "a": x["days_min"], "b": x["days_max"],
             "w": x["weight_g"], "id": s.id})
    await session.execute(text("""
        UPDATE orders SET delivery_days = :d, delivery_tariff = :t, delivery_cdek_code = :c
         WHERE id = :o"""), {"d": opt["days"], "t": opt["tariff"], "c": code, "o": order_id})
    await sync_totals(session, order_id)


@router.post("/api/manage/orders/{order_id}/recalc")
async def recalc_delivery(order_id: int, payload: Recalc,
                          session: AsyncSession = Depends(get_session),
                          user=Depends(require_role("manager"))):
    """Цена доставки заново — той же службой и тем же способом, но по
    нынешнему составу и адресу. Без apply — только показать, с apply —
    записать в посылки и сумму."""
    o = await _order(session, order_id)
    if not o.delivery_carrier:
        raise HTTPException(422, "У заказа нет службы доставки — цену ставят вручную")
    if payload.apply and (why := money_locked(o)):
        raise HTTPException(409, why)
    opt, ships, code = await quote_order(
        session, o, " — поменяйте адрес или поставьте цену у посылок вручную")
    old = sum((s.price or 0 for s in ships), Decimal(0))
    out = {"old": str(old), "new": str(opt["price"]), "days": opt["days"],
           "parcels": [{"id": s.id, "from_city": x["from_city"], "old": str(s.price or 0),
                        "new": str(x["price"]), "days": x["days"]}
                       for s, x in zip(ships, opt["parcels"])]}
    if payload.apply:
        await apply_quote(session, order_id, opt, ships, code)
        if old != opt["price"]:
            await order_log.log(session, order_id, "delivery",
                                f"Доставка пересчитана: {money(old)} → {money(opt['price'])}", user)
        await session.commit()
    return out


# ------------------------------------------------------------------
# Заметки в ленте
# ------------------------------------------------------------------


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/api/manage/orders/{order_id}/notes")
async def add_note(order_id: int, payload: Note, session: AsyncSession = Depends(get_session),
                   user=Depends(current_user)):
    """Комментарий для коллег — как в ленте сделки: «звонил, не берёт
    трубку», «просит отправить в понедельник». Покупатель его не видит."""
    await _order(session, order_id)
    body = payload.text.strip()
    if not body:
        raise HTTPException(422, "Напишите комментарий")
    await order_log.log(session, order_id, "note", body, user)
    await session.commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Посылки заказа
# ------------------------------------------------------------------
# Каждый филиал собирает и отправляет свою посылку сам: вписывает номер
# отслеживания и отмечает, что отправил. Ушли все посылки — заказ
# «отправлен», доставлены все — «выдан».

SHIPMENT_FLOW = {"assembling", "sent", "delivered"}
TRACK_RE = r"^[A-Za-z0-9-]{4,40}$"


class ShipmentPatch(BaseModel):
    status: str | None = None
    track_number: str | None = Field(default=None, max_length=40)
    # Цена посылки — менеджер договорился о другой или пересчитал сам
    price: Decimal | None = Field(default=None, ge=0, le=1_000_000)


@router.patch("/api/manage/shipments/{shipment_id}")
async def patch_shipment(
    shipment_id: int,
    payload: ShipmentPatch,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("manager")),
):
    row = (await session.execute(text("""
        SELECT s.id, s.order_id, s.status, s.carrier, s.track_number, s.price, b.city, b.name,
               o.status::text AS order_status
          FROM order_shipments s JOIN orders o ON o.id = s.order_id
          LEFT JOIN branches b ON b.id = s.branch_id
         WHERE s.id = :id"""), {"id": shipment_id})).first()
    if not row:
        raise HTTPException(404, "Посылка не найдена")
    if row.order_status == "cancelled" or row.status == "cancelled":
        raise HTTPException(409, "Заказ отменён — посылку не отправляют")
    if payload.status is not None and payload.status not in SHIPMENT_FLOW:
        raise HTTPException(422, "Неизвестный статус посылки")
    track = None
    if payload.track_number is not None:
        track = payload.track_number.strip().replace(" ", "")
        if track and not re.match(TRACK_RE, track):
            raise HTTPException(422, "Номер отслеживания: латиница, цифры и дефис, 4–40 знаков")
    # СДЭК и Почта дают номер при приёме посылки — без него покупатель
    # её не найдёт. Яндекс присылает отслеживание получателю сам
    if (payload.status in ("sent", "delivered") and row.carrier in ("cdek", "pochta")
            and not (track if payload.track_number is not None else row.track_number)):
        raise HTTPException(422, "Впишите номер отслеживания — без него покупатель "
                                 "не найдёт посылку")
    price_changed = payload.price is not None and payload.price != (row.price or 0)
    if price_changed and (why := money_locked(await _order(session, row.order_id))):
        raise HTTPException(409, why)

    await session.execute(text("""
        UPDATE order_shipments
           SET track_number = CASE WHEN CAST(:t AS text) IS NULL THEN track_number
                                   ELSE nullif(CAST(:t AS text), '') END,
               status = coalesce(CAST(:st AS text), status),
               price = coalesce(CAST(:p AS numeric), price),
               sent_at = CASE WHEN coalesce(CAST(:st AS text), status) IN ('sent', 'delivered')
                              THEN coalesce(sent_at, now()) ELSE NULL END,
               delivered_at = CASE WHEN coalesce(CAST(:st AS text), status) = 'delivered'
                                   THEN coalesce(delivered_at, now()) ELSE NULL END
         WHERE id = :id"""),
        {"t": track, "st": payload.status, "p": payload.price, "id": shipment_id})

    what = []
    frm = ship_services.city_from(row.city)
    if payload.status and payload.status != row.status:
        what.append(f"{SHIPMENT_LABELS.get(row.status)} → {SHIPMENT_LABELS.get(payload.status)}")
    if payload.track_number is not None and (track or None) != (row.track_number or None):
        what.append(f"номер {track or '—'}")
    if price_changed:
        what.append(f"цена {money(row.price)} → {money(payload.price)}")
        await sync_totals(session, row.order_id)
    if what:
        await order_log.log(session, row.order_id, "shipment",
                            f"Посылка {frm}: " + ", ".join(what), user)

    # Заказ следует за посылками — только вперёд: вернуть посылку в
    # «собирается» не откатывает заказ, это решает менеджер
    left = (await session.execute(text("""
        SELECT count(*) FILTER (WHERE status = 'assembling') AS assembling,
               count(*) FILTER (WHERE status = 'sent') AS sent
          FROM order_shipments WHERE order_id = :o AND status <> 'cancelled'"""),
        {"o": row.order_id})).first()
    nxt = None
    if not left.assembling and not left.sent and row.order_status != "completed":
        nxt = "completed"
    elif not left.assembling and row.order_status in ("new", "confirmed", "paid"):
        nxt = "shipped"
    if nxt:
        await set_order_status(session, row.order_id, nxt)
        await order_log.log(session, row.order_id, "status",
                            f"Статус: {ORDER_LABELS.get(row.order_status)} → {ORDER_LABELS.get(nxt)}"
                            " — по посылкам")
    await session.commit()
    return {"ok": True, "order_status": nxt or row.order_status}
