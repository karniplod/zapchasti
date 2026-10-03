"""Правка заказа покупателем в личном кабинете — пока заказ «новый».

Пока менеджер не взял заказ в работу, покупатель сам поправит то, в чём
ошибся: получателя и телефон, адрес (служба и способ доставки — те же),
филиал самовывоза, количество деталей и комментарий; добавит деталь,
которую забыл, — из формы правки или со страницы детали. Может и
отменить заказ. Подтверждён, оплачен или отправлен — правит только
менеджер.

Добавленная деталь из другого филиала едет своей посылкой: она
появляется в заказе, и доставка пересчитывается по всем посылкам.

Цену доставки считает сервер — по новому адресу и составу, той же
службой. Перед сохранением покупатель видит «было → станет»: тот же
код правки выполняется и откатывается (/preview), так что показанное
и сохранённое не расходятся.

Изменилась сумма — неоплаченные ссылки на оплату аннулируются: по ним
заплатили бы старую сумму (см. payments.settle).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import customer_auth as ca
from .. import order_log, payments
from ..auth import current_user
from ..config import settings
from ..database import get_session
from ..templating import templates
from .orders_admin import (_shipment_for, apply_quote, money, quote_order, return_stock,
                           set_order_status, shipping_address, sync_totals, take_stock)
from .shop import receipt_of, start_payment

router = APIRouter(tags=["account-orders"])


def customer_can_edit(o) -> bool:
    """Покупатель правит заказ, пока он новый и не оплачен."""
    return o["status"] == "new" and not o["paid_at"]


class ItemQty(BaseModel):
    id: int
    qty: int = Field(ge=0, le=999)            # 0 — убрать из заказа


class AddPart(BaseModel):
    part_id: int
    qty: int = Field(default=1, ge=1, le=999)


class CustomerEdit(BaseModel):
    contact_name: str | None = Field(default=None, max_length=120)
    contact_phone: str | None = Field(default=None, max_length=32)
    comment: str | None = Field(default=None, max_length=1000)
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
    items: list[ItemQty] | None = Field(default=None, max_length=100)
    # Детали, которые покупатель добавляет к заказу
    add: list[AddPart] | None = Field(default=None, max_length=20)


ADDRESS = {"delivery_city", "delivery_cdek_code", "delivery_postcode", "delivery_country",
           "delivery_street", "delivery_house", "delivery_block", "delivery_flat", "delivery_point"}


async def _own(session: AsyncSession, customer: dict, number: str, lock: bool = True):
    row = (await session.execute(text(f"""
        SELECT o.*, o.status::text AS status FROM orders o
         WHERE o.number = :n AND o.customer_id = :c {'FOR UPDATE' if lock else ''}"""),
        {"n": number, "c": customer["id"]})).first()
    if not row:
        raise HTTPException(404, "Заказ не найден")
    if not customer_can_edit(row._mapping):
        raise HTTPException(409, "Заказ уже в работе — изменить его может менеджер. "
                                 "Позвоните нам или напишите в контактах")
    return row


async def _reload(session, order_id):
    return (await session.execute(text("""
        SELECT o.*, o.status::text AS status FROM orders o WHERE o.id = :id"""),
        {"id": order_id})).first()


async def apply_edit(session: AsyncSession, o, payload: CustomerEdit) -> dict:
    """Правка в открытой транзакции — без commit: /preview её откатит,
    PATCH запишет. → итоги «было → станет» и список изменений."""
    sent = payload.model_fields_set
    changes: list[str] = []
    old_total, old_ship = o.total, o.delivery_price or 0
    old_parcels = (await session.execute(text(
        "SELECT count(*) FROM order_shipments WHERE order_id = :o AND status <> 'cancelled'"),
        {"o": o.id})).scalar()

    # --- получатель и комментарий ----------------------------------
    upd: dict = {}
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
    if "comment" in sent:
        upd["comment"] = (payload.comment or "").strip() or None
    labels = {"contact_name": "Получатель", "contact_phone": "Телефон", "comment": "Комментарий"}
    for f, v in upd.items():
        if (getattr(o, f) or None) != v:
            changes.append(f"{labels[f]}: {getattr(o, f) or '—'} → {v or '—'}")

    # --- состав ----------------------------------------------------
    if payload.items:
        rows = {r.id: r for r in (await session.execute(text("""
            SELECT oi.id, oi.part_id, oi.qty, oi.shipment_id, p.sku, p.name, p.quantity
              FROM order_items oi JOIN parts p ON p.id = oi.part_id
             WHERE oi.order_id = :o"""), {"o": o.id})).all()}
        want = {i.id: i.qty for i in payload.items}
        if set(want) - set(rows):
            raise HTTPException(422, "В заказе нет такой детали — обновите страницу")
        if all(want.get(i, r.qty) == 0 for i, r in rows.items()) and not payload.add:
            raise HTTPException(409, "В заказе должна остаться хотя бы одна деталь — "
                                     "чтобы отказаться от всего, отмените заказ")
        for iid, qty in want.items():
            r = rows[iid]
            if qty == r.qty:
                continue
            if qty == 0:
                await return_stock(session, r.part_id, r.qty)
                await session.execute(text("DELETE FROM order_items WHERE id = :i"), {"i": iid})
                changes.append(f"Убрано: {r.sku} {r.name}")
            elif qty > r.qty:
                if not await take_stock(session, r.part_id, qty - r.qty):
                    raise HTTPException(409, f"{r.name}: в наличии ещё только {r.quantity} шт.")
                await session.execute(text("UPDATE order_items SET qty = :q WHERE id = :i"),
                                      {"q": qty, "i": iid})
                changes.append(f"{r.sku}: {r.qty} → {qty} шт")
            else:
                await return_stock(session, r.part_id, r.qty - qty)
                await session.execute(text("UPDATE order_items SET qty = :q WHERE id = :i"),
                                      {"q": qty, "i": iid})
                changes.append(f"{r.sku}: {r.qty} → {qty} шт")
        # Посылка опустела — её больше нет
        await session.execute(text("""
            DELETE FROM order_shipments s WHERE s.order_id = :o
               AND NOT EXISTS (SELECT 1 FROM order_items WHERE shipment_id = s.id)"""),
            {"o": o.id})

    # --- добавленные детали ------------------------------------------
    for a in payload.add or []:
        p = (await session.execute(text("""
            SELECT id, sku, name, price, quantity, branch_id FROM parts
             WHERE id = :p AND status = 'in_stock' AND published AND price IS NOT NULL"""),
            {"p": a.part_id})).first()
        if not p:
            raise HTTPException(409, "Этой детали уже нет в продаже — обновите страницу")
        if not await take_stock(session, p.id, a.qty):
            raise HTTPException(409, f"{p.name}: в наличии только {p.quantity} шт.")
        have = (await session.execute(text("""
            SELECT id FROM order_items WHERE order_id = :o AND part_id = :p"""),
            {"o": o.id, "p": p.id})).scalar()
        if have:
            await session.execute(text("UPDATE order_items SET qty = qty + :q WHERE id = :i"),
                                  {"q": a.qty, "i": have})
        else:
            # Посылка филиала детали: есть — в неё, нет — новая, её цену
            # даст пересчёт доставки ниже
            sid = await _shipment_for(session, o, p.branch_id) if o.delivery_carrier else None
            await session.execute(text("""
                INSERT INTO order_items (order_id, part_id, price, qty, shipment_id, source)
                VALUES (:o, :p, :pr, :q, :s, 'customer')"""),
                {"o": o.id, "p": p.id, "pr": p.price, "q": a.qty, "s": sid})
        changes.append(f"Добавлено: {p.sku} {p.name}" + (f" × {a.qty}" if a.qty > 1 else ""))

    # --- получение -------------------------------------------------
    if "pickup_branch_id" in sent and o.delivery_method == "pickup":
        b = (await session.execute(text("""
            SELECT id, city || ', ' || name AS title FROM branches WHERE id = :b AND is_active"""),
            {"b": payload.pickup_branch_id})).first()
        if not b:
            raise HTTPException(422, "Выберите филиал для самовывоза")
        if b.id != o.pickup_branch_id:
            upd["pickup_branch_id"] = b.id
            changes.append(f"Самовывоз: {b.title}")
    if o.delivery_method == "shipping" and sent & ADDRESS:
        a_upd, a_changes, _ = await shipping_address(session, o, payload, sent)
        upd.update(a_upd)
        changes += a_changes

    if upd:
        sets = ", ".join(f"{k} = :{k}" for k in upd)
        await session.execute(text(f"UPDATE orders SET {sets}, updated_at = now() WHERE id = :id"),
                              {**upd, "id": o.id})

    # --- доставка заново: адрес или вес могли поменяться -------------
    o2 = await _reload(session, o.id)
    if o2.delivery_carrier and (payload.items or payload.add or sent & ADDRESS):
        opt, ships, code = await quote_order(
            session, o2, " — выберите другой адрес или свяжитесь с нами")
        await apply_quote(session, o.id, opt, ships, code)
    await sync_totals(session, o.id)
    o2 = await _reload(session, o.id)
    if o2.delivery_price != o.delivery_price and o.delivery_carrier:
        changes.append(f"Доставка: {money(old_ship)} → {money(o2.delivery_price)}")
    goods = (await session.execute(text(
        "SELECT coalesce(sum(price * qty), 0) FROM order_items WHERE order_id = :o"),
        {"o": o.id})).scalar()
    return {"old_total": str(old_total), "total": str(o2.total), "goods": str(goods),
            "old_delivery": str(old_ship), "delivery": str(o2.delivery_price or 0),
            "days": o2.delivery_days, "changes": changes,
            "old_parcels": old_parcels, "parcels": (await session.execute(text(
        "SELECT count(*) FROM order_shipments WHERE order_id = :o AND status <> 'cancelled'"),
        {"o": o.id})).scalar()}


@router.post("/api/account/orders/{number}/preview")
async def preview_edit(number: str, payload: CustomerEdit,
                       session: AsyncSession = Depends(get_session),
                       customer: dict = Depends(ca.current_customer)):
    """Что станет с суммой — без сохранения: правка выполняется и
    откатывается."""
    o = await _own(session, customer, number)
    try:
        return await apply_edit(session, o, payload)
    finally:
        await session.rollback()


@router.patch("/api/account/orders/{number}")
async def save_edit(number: str, payload: CustomerEdit,
                    session: AsyncSession = Depends(get_session),
                    customer: dict = Depends(ca.current_customer)):
    o = await _own(session, customer, number)
    try:
        out = await apply_edit(session, o, payload)
    except Exception:
        await session.rollback()
        raise
    if not out["changes"]:
        await session.rollback()
        return {**out, "saved": False}
    # Сумма другая — старые ссылки на оплату больше не годятся
    if out["total"] != out["old_total"]:
        await session.execute(text("""
            UPDATE payments SET status = 'cancelled'
             WHERE order_id = :o AND status = 'pending'"""), {"o": o.id})
    await order_log.log(session, o.id, "edit", "Покупатель изменил заказ: " + "; ".join(out["changes"]))
    await session.commit()
    return {**out, "saved": True}


@router.post("/api/account/orders/{number}/cancel")
async def cancel_order(number: str, session: AsyncSession = Depends(get_session),
                       customer: dict = Depends(ca.current_customer)):
    """Покупатель передумал: детали возвращаются на витрину, посылки
    отменяются, неоплаченные ссылки на оплату — тоже."""
    o = await _own(session, customer, number)
    await set_order_status(session, o.id, "cancelled")
    await session.execute(text("""
        UPDATE payments SET status = 'cancelled' WHERE order_id = :o AND status = 'pending'"""),
        {"o": o.id})
    await order_log.log(session, o.id, "status", "Покупатель отменил заказ в личном кабинете")
    await session.commit()
    return {"ok": True}



# ------------------------------------------------------------------
# Способ оплаты
# ------------------------------------------------------------------
# Выбрал Робокассу, а решил платить при получении — или наоборот. Пока
# заказ не оплачен, способ меняется в кабинете: онлайн — сразу к оплате,
# «при получении» — неоплаченные ссылки аннулируются, чтобы по ним не
# заплатили второй раз.

class PaymentChoice(BaseModel):
    method: str = Field(max_length=16)        # on_receipt / card / sbp / robokassa


async def switch_payment(session: AsyncSession, o, method: str, who: dict | None = None) -> bool:
    """Записать новый способ оплаты; ссылки другими способами аннулировать.
    Без commit. → поменялся ли способ."""
    online = method != "on_receipt"
    if online and not payments.method(method):
        raise HTTPException(422, "Этот способ оплаты сейчас недоступен")
    old = payments.title_of(o.pay_with) if o.payment_method == "online" and o.pay_with else (
        "онлайн" if o.payment_method == "online" else "при получении")
    new = payments.title_of(method) if online else "при получении"
    # Ссылка тем же способом остаётся: её и откроем снова
    await session.execute(text("""
        UPDATE payments SET status = 'cancelled'
         WHERE order_id = :o AND status = 'pending' AND method IS DISTINCT FROM :m"""),
        {"o": o.id, "m": method if online else None})
    if (o.payment_method, o.pay_with) == ("online" if online else "on_receipt", method if online else None):
        return False
    await session.execute(text("""
        UPDATE orders SET payment_method = :pm, pay_with = :pw, updated_at = now() WHERE id = :o"""),
        {"pm": "online" if online else "on_receipt", "pw": method if online else None, "o": o.id})
    await order_log.log(session, o.id, "payment",
                        ("Покупатель сменил" if who is None else "Сменён") + f" способ оплаты: {old} → {new}",
                        who)
    return True


@router.post("/api/account/orders/{number}/payment")
async def choose_payment(number: str, payload: PaymentChoice, request: Request,
                         session: AsyncSession = Depends(get_session),
                         customer: dict = Depends(ca.current_customer)):
    o = (await session.execute(text("""
        SELECT o.*, o.status::text AS status FROM orders o
         WHERE o.number = :n AND o.customer_id = :c FOR UPDATE"""),
        {"n": number, "c": customer["id"]})).first()
    if not o:
        raise HTTPException(404, "Заказ не найден")
    if o.paid_at or o.status not in ("new", "confirmed"):
        raise HTTPException(409, "Заказ уже оплачен или отменён — способ оплаты не меняется")
    await switch_payment(session, o, payload.method)
    await session.commit()
    if payload.method == "on_receipt":
        return {"ok": True}
    # Онлайн — сразу к оплате. Провайдер не ответил — способ уже выбран,
    # оплатить можно снова с этой же страницы
    try:
        url = await start_payment(session, {"id": o.id, "number": o.number, "total": o.total},
                                  payload.method, request)
    except payments.PaymentError as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True, "redirect_url": url}


# ------------------------------------------------------------------
# Чек об оплате
# ------------------------------------------------------------------
# Страница в кабинете: что оплачено, кем продано, когда и как. Позиции —
# те же, что уходят в кассовый чек ЮKassa (shop.receipt_of). Сам
# кассовый чек по 54-ФЗ присылает касса провайдера — об этом на чеке
# сказано, куда он отправлен.

VAT_LABELS = {1: "без НДС", 2: "НДС 0%", 3: "НДС 10%", 4: "НДС 20%",
              5: "НДС 10/110", 6: "НДС 20/120"}


async def receipt_context(session: AsyncSession, order_id: int) -> dict:
    o = (await session.execute(text("""
        SELECT o.*, o.status::text AS status FROM orders o WHERE o.id = :o"""),
        {"o": order_id})).first()
    if not o or not o.paid_at:
        raise HTTPException(404, "Заказ ещё не оплачен — чека пока нет")
    pay = (await session.execute(text("""
        SELECT method, provider, amount, paid_at, external_id FROM payments
         WHERE order_id = :o AND status = 'paid' ORDER BY paid_at DESC LIMIT 1"""),
        {"o": order_id})).first()
    rc = await receipt_of(session, order_id)
    lines = [{**i, "sum": i["price"] * i["qty"]} for i in rc["items"]]
    if pay:
        how = payments.title_of(pay.method) + " (онлайн)"
        fiscal = (f"Кассовый чек по 54-ФЗ отправлен на {rc['email'] or phone_label(rc['phone'])}"
                  if pay.provider in ("yookassa", "robokassa") and (rc["email"] or rc["phone"])
                  else "Кассовый чек по 54-ФЗ формирует касса платёжного сервиса")
    else:
        how = "при получении"
        fiscal = "Кассовый чек выдаётся при получении и оплате заказа"
    return {
        "order": dict(o._mapping), "lines": lines, "total": sum(i["sum"] for i in lines),
        "paid_at": pay.paid_at if pay else o.paid_at, "how": how,
        "payment_id": pay.external_id if pay else None, "fiscal": fiscal,
        "vat": VAT_LABELS.get(settings.yookassa_vat_code, "без НДС"),
        "seller": {"name": settings.seller_name, "inn": settings.seller_inn,
                   "ogrn": settings.seller_ogrn, "address": settings.seller_address},
        "site": settings.base_url.split("//")[-1],
    }


def phone_label(p: str | None) -> str:
    d = "".join(ch for ch in (p or "") if ch.isdigit())
    return f"+7 {d[1:4]} {d[4:7]}-{d[7:9]}-{d[9:]}" if len(d) == 11 else (p or "")


@router.get("/account/orders/{number}/receipt", response_class=HTMLResponse)
async def receipt_page(number: str, request: Request, session: AsyncSession = Depends(get_session),
                       customer: dict = Depends(ca.current_customer)):
    oid = (await session.execute(text("""
        SELECT id FROM orders WHERE number = :n AND customer_id = :c"""),
        {"n": number, "c": customer["id"]})).scalar()
    if not oid:
        raise HTTPException(404, "Заказ не найден")
    return templates.TemplateResponse("shop/receipt.html", {
        "request": request, "user": None, "customer": customer,
        "back": f"/account/orders/{number}", **await receipt_context(session, oid)})


@router.get("/orders/{number}/receipt", response_class=HTMLResponse)
async def receipt_page_staff(number: str, request: Request,
                             session: AsyncSession = Depends(get_session),
                             user=Depends(current_user)):
    """Тот же чек — для менеджера: покупатель просит прислать его ещё раз."""
    oid = (await session.execute(text("SELECT id FROM orders WHERE number = :n"),
                                 {"n": number})).scalar()
    if not oid:
        raise HTTPException(404, "Заказ не найден")
    return templates.TemplateResponse("shop/receipt.html", {
        "request": request, "user": None, "customer": None,
        "back": f"/orders/{number}", **await receipt_context(session, oid)})
