"""Покупательская часть: корзина, заказ, оплата, личный кабинет.

У детали есть остаток — parts.quantity, обычно 1, у одинаковых (четыре
диска с одной машины) больше. В корзине — сколько штук берут, не больше
остатка. Заказ списывает штуки сразу, при оформлении: иначе двое купят
один и тот же бампер. Остаток дошёл до нуля — деталь уходит в reserved
и пропадает с витрины; отмена заказа возвращает штуки.
"""

import secrets
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import address as addr
from .. import customer_auth as ca
from .. import delivery as ship_services
from .. import mailer, order_log, payments
from ..config import settings
from ..database import get_session
from ..templating import templates

router = APIRouter(tags=["shop"])

CART_COOKIE = "razbor_cart"

NAME_RE = ca.NAME_RE

# Способы онлайн-оплаты — из app/payments.py: без ключей ЮKassa список
# пуст, и заказ оплачивается при получении, а менеджер отмечает оплату
# в бэкенде
PAYMENT_LABELS = {"online": "онлайн", "on_receipt": "при получении"}
PAYMENT_STATUS = {"pending": "ожидает оплаты", "paid": "оплачен", "failed": "не прошёл",
                  "cancelled": "аннулирован — сумма заказа изменилась"}

ORDER_LABELS = {
    "new": "новый",
    "confirmed": "подтверждён",
    "paid": "оплачен",
    "shipped": "отправлен",
    "completed": "выдан",
    "cancelled": "отменён",
}


# ------------------------------------------------------------------
# Корзина
# ------------------------------------------------------------------


def cart_token(request: Request) -> str | None:
    return request.cookies.get(CART_COOKIE)


async def cart_rows(session: AsyncSession, token: str | None, customer: dict | None):
    """Содержимое корзины.

    Ищем и по куке, и по покупателю: человек мог набрать корзину
    на телефоне, а оформлять с ноутбука — там кука другая, а вход тот же.
    """
    if not token and not customer:
        return []

    # Одна и та же деталь могла лечь и в корзину этого браузера, и в
    # корзину кабинета с другого устройства — показываем одной строкой
    rows = await session.execute(
        text("""
        SELECT p.id AS part_id, p.sku, p.name, p.price, p.status::text AS status,
               p.condition::text AS condition, p.quantity AS stock,
               p.size_class, p.weight_kg,
               max(ci.qty) AS qty,
               (SELECT coalesce(ph.thumb, ph.path) FROM part_photos ph
                 WHERE ph.part_id = p.id ORDER BY sort_order LIMIT 1) AS photo,
               (SELECT br.city || ', ' || br.name FROM branches br
                 WHERE br.id = p.branch_id) AS branch,
               p.branch_id,
               min(ci.added_at) AS added_at
          FROM cart_items ci
          JOIN parts p ON p.id = ci.part_id
         WHERE ci.cart_token = coalesce(:t, '')
            OR (ci.customer_id IS NOT NULL AND ci.customer_id = :c)
         GROUP BY p.id
         ORDER BY min(ci.added_at)
    """),
        {"t": token, "c": customer["id"] if customer else None},
    )
    out = []
    for r in rows:
        i = dict(r._mapping)
        # Пока лежало в корзине, остаток мог уменьшиться — берём сколько есть
        i["short"] = i["status"] == "in_stock" and i["qty"] > i["stock"]
        i["take"] = min(i["qty"], i["stock"]) if i["status"] == "in_stock" else 0
        i["sum"] = (i["price"] or 0) * i["take"]
        out.append(i)
    return out


@router.get("/cart", response_class=HTMLResponse)
async def cart_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict | None = Depends(ca.optional_customer),
):
    items = await cart_rows(session, cart_token(request), customer)
    # Пока деталь лежала в корзине, часть штук купили — сразу уменьшаем
    # количество до остатка, чтобы оформление не упёрлось в «столько нет»
    for i in items:
        if i["short"] and i["take"]:
            await session.execute(text("""
                UPDATE cart_items SET qty = :q
                 WHERE part_id = :p AND (cart_token = coalesce(:t, '')
                       OR (customer_id IS NOT NULL AND customer_id = :c))"""),
                {"q": i["take"], "p": i["part_id"], "t": cart_token(request),
                 "c": customer["id"] if customer else None})
    await session.commit()
    branches = [dict(r._mapping) for r in await session.execute(text("""
        SELECT id, city || ', ' || name AS label FROM branches
         WHERE is_active ORDER BY sort_order, city, name"""))]
    live = [i for i in items if i["status"] == "in_stock"]
    # Самовывоз по умолчанию — из филиала, где лежит больше деталей заказа
    here = [i["branch_id"] for i in live if i["branch_id"]]
    pickup = max(set(here), key=here.count) if here else None
    return templates.TemplateResponse(
        "shop/cart.html",
        {
            "request": request,
            "user": None,
            "customer": customer,
            "items": items,
            "branches": branches,
            "pickup": pickup,
            # Детали из разных филиалов — предупредить, что соберём в один
            "spread": len(set(here)) > 1,
            "methods": payments.methods(),
            "carriers": ship_services.enabled(),
            "address_hints": addr.enabled(),
            # Деталь могли продать, пока она лежала в корзине
            "gone": [i for i in items if i["status"] != "in_stock"],
            "no_price": [i for i in items if i["price"] is None],
            "short": [i for i in items if i["short"]],
            "units": sum(i["take"] for i in items),
            "total": sum(i["sum"] for i in items if i["price"] is not None),
        },
    )


class CartAdd(BaseModel):
    sku: str = Field(max_length=32)
    qty: int = Field(default=1, ge=1, le=999)


class CartQty(BaseModel):
    qty: int = Field(ge=1, le=999)


@router.post("/api/cart", status_code=201)
async def cart_add(
    payload: CartAdd,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    customer: dict | None = Depends(ca.optional_customer),
):
    part = (
        await session.execute(
            text("""
        SELECT id, status::text AS status, published, quantity
          FROM parts WHERE sku = :s
    """),
            {"s": payload.sku},
        )
    ).first()

    if not part or not part.published:
        raise HTTPException(404, "Деталь не найдена")
    if part.status != "in_stock":
        raise HTTPException(409, "Эту деталь уже забрали")

    token = cart_token(request) or secrets.token_urlsafe(24)
    # Уже в корзине — прибавляем штуки, но не больше, чем есть на складе
    await session.execute(
        text("""
        INSERT INTO cart_items (cart_token, customer_id, part_id, qty)
        VALUES (:t, :c, :p, least(CAST(:q AS int), CAST(:stock AS int)))
        ON CONFLICT (cart_token, part_id)
        DO UPDATE SET qty = least(cart_items.qty + CAST(:q AS int), CAST(:stock AS int))
    """),
        {"t": token, "c": customer["id"] if customer else None, "p": part.id,
         "q": payload.qty, "stock": max(part.quantity, 1)},
    )
    await session.commit()

    count = sum(i["take"] for i in await cart_rows(session, token, customer))
    # Кука ставится на ответ, а не заранее: корзины может и не быть
    response.set_cookie(CART_COOKIE, token, max_age=30 * 86400,
                        httponly=True, samesite="lax", path="/")
    return {"count": count}


@router.patch("/api/cart/{part_id}")
async def cart_set_qty(
    part_id: int,
    payload: CartQty,
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict | None = Depends(ca.optional_customer),
):
    """Кнопки − и + в корзине. Больше остатка не даём: ответ говорит,
    сколько штук в итоге и сколько вообще есть."""
    stock = (await session.execute(
        text("SELECT quantity FROM parts WHERE id = :p AND status = 'in_stock'"),
        {"p": part_id})).scalar()
    if not stock:
        raise HTTPException(409, "Эту деталь уже забрали")
    qty = min(payload.qty, stock)
    await session.execute(
        text("""
        UPDATE cart_items SET qty = :q
         WHERE part_id = :p
           AND (cart_token = coalesce(:t, '')
                OR (customer_id IS NOT NULL AND customer_id = :c))
    """),
        {"q": qty, "p": part_id, "t": cart_token(request),
         "c": customer["id"] if customer else None},
    )
    await session.commit()
    return {"qty": qty, "stock": stock, "capped": qty < payload.qty}


@router.delete("/api/cart/{part_id}", status_code=204)
async def cart_remove(
    part_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict | None = Depends(ca.optional_customer),
):
    await session.execute(
        text("""
        DELETE FROM cart_items
         WHERE part_id = :p
           AND (cart_token = coalesce(:t, '')
                OR (customer_id IS NOT NULL AND customer_id = :c))
    """),
        {"p": part_id, "t": cart_token(request),
         "c": customer["id"] if customer else None},
    )
    await session.commit()
    return Response(status_code=204)


@router.get("/api/me")
async def me(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict | None = Depends(ca.optional_customer),
):
    """Один запрос на всю шапку: и вход, и счётчик корзины.

    Шапка общая для всех страниц витрины, а данные о покупателе есть
    не в каждом обработчике — проще спросить отсюда, чем протаскивать
    их через каждый TemplateResponse.
    """
    items = await cart_rows(session, cart_token(request), customer)
    return {
        "authorized": bool(customer),
        # Без имени — телефон или начало email: целиком адрес не влезает в шапку
        "name": (customer or {}).get("name") or (customer or {}).get("phone")
                or ((customer or {}).get("email") or "").split("@")[0] or None,
        "cart": sum(i["take"] for i in items),
    }


@router.get("/api/cart")
async def cart_count(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict | None = Depends(ca.optional_customer),
):
    """Счётчик для шапки."""
    items = await cart_rows(session, cart_token(request), customer)
    return {"count": sum(i["take"] for i in items)}


# ------------------------------------------------------------------
# Вход покупателя
# ------------------------------------------------------------------


@router.get("/account/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/account", error: str | None = None):
    from .oauth import enabled, safe_next  # oauth импортирует shop — здесь, а не наверху

    return templates.TemplateResponse(
        "shop/login.html",
        {"request": request, "user": None, "customer": None,
         "next": safe_next(next), "error": error, "providers": enabled(),
         # Числовой id бота — начало токена до двоеточия; сам токен
         # на страницу не попадает
         "telegram_bot_id": settings.telegram_bot_token.split(":")[0]
                            if any(p["code"] == "telegram" for p in enabled()) else ""},
    )


class Credentials(BaseModel):
    # Телефон или email — одно поле: человек вводит то, что помнит
    login: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=6, max_length=200)
    name: str | None = Field(default=None, max_length=120)


async def adopt_cart(session: AsyncSession, token: str | None, customer_id: int) -> None:
    """Корзину, собранную до входа, привязываем к покупателю.

    Без этого человек добавляет деталь, идёт регистрироваться и
    возвращается к пустой корзине.
    """
    if not token:
        return
    await session.execute(
        text("""
        UPDATE cart_items SET customer_id = :c
         WHERE cart_token = :t AND customer_id IS DISTINCT FROM :c
    """),
        {"c": customer_id, "t": token},
    )
    await session.commit()


@router.post("/api/account/register", status_code=201)
async def register(
    payload: Credentials,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    who = ca.parse_login(payload.login)
    if not who:
        raise HTTPException(422, "Укажите телефон в формате +7 900 000-00-00 или email")
    if payload.name and not NAME_RE.match(payload.name.strip()):
        raise HTTPException(422, "В имени — только буквы, пробел и дефис")
    # Пароль из одних цифр подбирается за минуты
    if not (any(c.isalpha() for c in payload.password)
            and any(c.isdigit() for c in payload.password)):
        raise HTTPException(422, "В пароле нужны и буквы, и цифры")
    kind, value = who

    customer = await ca.register(
        session,
        value if kind == "phone" else None,
        payload.password,
        payload.name,
        value if kind == "email" else None,
    )
    # Регистрация по email — только после перехода по ссылке из письма.
    # Почта не настроена — подтверждать нечем, кабинет открывается сразу
    if kind == "email" and mailer.enabled():
        await adopt_cart(session, cart_token(request), customer["id"])
        sent = await send_verification(session, customer["id"], value, safe_next_url(request))
        return {"confirm": True, "email": value, "sent": sent}

    await adopt_cart(session, cart_token(request), customer["id"])
    ca.issue(response, customer["id"])
    return {"ok": True}


def safe_next_url(request: Request) -> str:
    """Куда вернуть после подтверждения — страница, с которой регистрировались."""
    raw = request.headers.get("x-next") or "/account"
    return raw if raw.startswith("/") and not raw.startswith("//") else "/account"


VERIFY_COOLDOWN_SEC = 60


async def send_verification(session: AsyncSession, customer_id: int, email: str,
                            next_url: str = "/account") -> bool:
    """Письмо со ссылкой подтверждения. Не чаще раза в минуту: кнопку
    «отправить ещё раз» жмут подряд, а почтовик за поток писем банит."""
    took = (await session.execute(text(f"""
        UPDATE customers SET email_verify_sent_at = now()
         WHERE id = :id AND (email_verify_sent_at IS NULL
               OR email_verify_sent_at < now() - interval '{VERIFY_COOLDOWN_SEC} seconds')
        RETURNING id"""), {"id": customer_id})).first()
    await session.commit()
    if not took:
        return False

    link = (f"{settings.base_url}/account/verify?"
            + urlencode({"t": ca.verify_token(customer_id, email), "next": next_url}))
    host = settings.base_url.split("://")[-1]
    text_body = (
        f"Здравствуйте!\n\n"
        f"Кто-то — надеемся, вы — зарегистрировался на {host} с этим адресом.\n"
        f"Чтобы подтвердить почту и войти в кабинет, откройте ссылку:\n\n{link}\n\n"
        f"Ссылка действует {ca.VERIFY_TTL_HOURS} часа. Если вы не регистрировались, "
        f"просто удалите письмо — без подтверждения кабинет не откроется.\n\n"
        f"{settings.app_name}"
    )
    html_body = templates.get_template("shop/email_verify.html").render(
        link=link, host=host, hours=ca.VERIFY_TTL_HOURS, app_name=settings.app_name)
    return await mailer.send(email, f"Подтвердите email — {settings.app_name}",
                             text_body, html_body)


class ResendIn(BaseModel):
    login: str = Field(min_length=3, max_length=200)


@router.post("/api/account/verify/resend")
async def resend_verification(
    payload: ResendIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Ещё одно письмо. Отвечает одинаково, есть такой адрес или нет:
    иначе по ответу можно было бы перебирать, чьи адреса у нас есть."""
    email = ca.normalize_email(payload.login)
    if not email:
        raise HTTPException(422, "Проверьте email: mail@example.ru")
    row = (await session.execute(text("""
        SELECT id FROM customers
         WHERE lower(email) = :e AND email_verified_at IS NULL"""), {"e": email})).first()
    if row:
        await send_verification(session, row.id, email, safe_next_url(request))
    return {"ok": True, "cooldown": VERIFY_COOLDOWN_SEC}


@router.get("/account/verify")
async def verify_email(
    request: Request,
    t: str = "",
    next: str = "/account",
    session: AsyncSession = Depends(get_session),
):
    """Переход по ссылке из письма: почта подтверждена, человек сразу
    в кабинете — второй раз вводить пароль незачем."""
    data = ca.read_verify_token(t)
    row = None
    if data:
        row = (await session.execute(text("""
            UPDATE customers SET email_verified_at = coalesce(email_verified_at, now()),
                                 last_login_at = now()
             WHERE id = :id AND lower(email) = :e
            RETURNING id"""), {"id": data["cid"], "e": data["e"]})).first()
        await session.commit()
    if not row:
        return templates.TemplateResponse("shop/verify_failed.html", {
            "request": request, "user": None, "customer": None,
            "hours": ca.VERIFY_TTL_HOURS,
        }, status_code=400)

    await adopt_cart(session, cart_token(request), row.id)
    dest = next if next.startswith("/") and not next.startswith("//") else "/account"
    response = RedirectResponse(dest + ("&" if "?" in dest else "?") + "verified=1",
                                status_code=303)
    ca.issue(response, row.id)
    return response


@router.post("/api/account/login")
async def login(
    payload: Credentials,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    who = ca.parse_login(payload.login)
    if not who:
        raise HTTPException(422, "Укажите телефон в формате +7 900 000-00-00 или email")

    customer = await ca.authenticate(session, *who, payload.password)
    if not customer:
        raise HTTPException(401, "Неверный логин или пароль")
    # Пароль верный, но почту так и не подтвердили — сначала письмо.
    # Ответ отдельным кодом: форма покажет кнопку «отправить ещё раз»
    if who[0] == "email" and not customer["email_verified"] and mailer.enabled():
        return JSONResponse({
            "detail": f"Подтвердите email: ссылка в письме на {customer['email']}. "
                      "Не пришло — проверьте «Спам» или отправьте ещё раз.",
            "code": "email_unverified", "email": customer["email"],
        }, status_code=403)

    await adopt_cart(session, cart_token(request), customer["id"])
    ca.issue(response, customer["id"])
    return {"ok": True}


@router.get("/account/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    ca.drop(response)
    return response


# ------------------------------------------------------------------
# Заказ
# ------------------------------------------------------------------


class OrderIn(BaseModel):
    contact_name: str = Field(min_length=1, max_length=80)
    contact_phone: str = Field(min_length=10, max_length=40)
    delivery_method: str = Field(default="pickup", max_length=32)
    pickup_branch_id: int | None = None
    delivery_address: str | None = Field(default=None, max_length=500)
    payment_method: str = Field(default="on_receipt", max_length=32)
    # Карта или СБП — для онлайн-оплаты
    pay_with: str | None = Field(default=None, max_length=16)
    comment: str | None = Field(default=None, max_length=1000)
    # Доставка службой: какая и как. Цену не передаём — сервер считает сам
    delivery_carrier: str | None = Field(default=None, max_length=16)
    delivery_mode: str | None = Field(default=None, max_length=8)
    delivery_city: str | None = Field(default=None, max_length=120)
    delivery_cdek_code: int | None = None
    delivery_postcode: str | None = Field(default=None, pattern=r"^\d{6}$")
    delivery_point: str | None = Field(default=None, max_length=64)
    # Адрес по полям — для курьера, Почты и доставки ТК. Сервер собирает
    # из них строку сам (app/address.py)
    delivery_country: str | None = Field(default=None, max_length=2)
    delivery_street: str | None = Field(default=None, max_length=120)
    delivery_house: str | None = Field(default=None, max_length=20)
    delivery_block: str | None = Field(default=None, max_length=20)
    delivery_flat: str | None = Field(default=None, max_length=20)
    # Согласие на обработку персональных данных (152-ФЗ): без него
    # имя и телефон хранить нельзя
    agree: bool = False


@router.post("/api/orders", status_code=201)
async def create_order(
    payload: OrderIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict = Depends(ca.current_customer),
):
    items = await cart_rows(session, cart_token(request), customer)
    if not items:
        raise HTTPException(409, "Корзина пуста")

    # Между добавлением в корзину и оформлением деталь могли продать
    gone = [i["sku"] for i in items if i["status"] != "in_stock"]
    if gone:
        raise HTTPException(409, f"Уже продано: {', '.join(gone)}. Уберите из корзины.")

    no_price = [i["sku"] for i in items if i["price"] is None]
    if no_price:
        raise HTTPException(
            409,
            f"Цена по запросу: {', '.join(no_price)}. "
            "Оставьте заявку — менеджер назовёт цену.",
        )

    # Проверка полей — та же, что в форме: скрипт можно выключить
    name = payload.contact_name.strip()
    if not NAME_RE.match(name):
        raise HTTPException(422, "Имя получателя — только буквы, пробел и дефис")
    phone = ca.normalize_phone(payload.contact_phone)
    if not phone:
        raise HTTPException(422, "Телефон получателя в формате +7 900 000-00-00")
    if payload.delivery_method not in ("pickup", "shipping"):
        raise HTTPException(422, "Выберите способ получения")
    address = (payload.delivery_address or "").strip()
    # Адрес по полям: проверяем каждое и собираем строку. Для пункта
    # выдачи адрес не нужен — его даёт сам пункт
    if (payload.delivery_method == "shipping" and payload.delivery_street is not None
            and payload.delivery_mode != "pvz"):
        address, err = addr.compose(
            payload.delivery_country or "RU", payload.delivery_postcode,
            payload.delivery_city or "", payload.delivery_street,
            payload.delivery_house or "", payload.delivery_block, payload.delivery_flat)
        if err:
            raise HTTPException(422, err)
        if len((payload.delivery_city or "").strip()) < 2:
            raise HTTPException(422, "Укажите город доставки")
    branch = None
    if payload.delivery_method == "pickup":
        branch = (await session.execute(
            text("SELECT id FROM branches WHERE id = :b AND is_active"),
            {"b": payload.pickup_branch_id})).scalar()
        if not branch:
            raise HTTPException(422, "Выберите филиал для самовывоза")
        address = ""
    ship = None
    if payload.delivery_method == "shipping" and payload.delivery_carrier:
        ship = await shipping_choice(session, request, customer, payload, address)
        address = ship["address"]
    elif payload.delivery_method == "shipping" and len(address) < 10:
        raise HTTPException(422, "Адрес доставки: город, улица, дом — не короче 10 символов")
    if payload.payment_method not in PAYMENT_LABELS:
        raise HTTPException(422, "Выберите способ оплаты")
    online = payload.payment_method == "online"
    if online and not payments.method(payload.pay_with or ""):
        raise HTTPException(422, "Онлайн-оплата сейчас недоступна — выберите оплату при получении")
    if not payload.agree:
        raise HTTPException(422, "Нужно согласие на обработку персональных данных")

    # Остатка стало меньше, чем в корзине, — не оформляем молча меньшее
    short = [f"{i['sku']} (есть {i['stock']} шт.)" for i in items if i["short"]]
    if short:
        raise HTTPException(409, f"Столько нет на складе: {', '.join(short)}. "
                                 "Уменьшите количество в корзине.")

    total = sum(i["price"] * i["qty"] for i in items) + (ship["price"] if ship else 0)
    number = f"{date.today().year}-{await next_order_number(session):06d}"

    order_id = (
        await session.execute(
            text("""
        INSERT INTO orders (number, customer_id, status, source, total,
                            delivery_method, delivery_address, comment,
                            contact_name, contact_phone, pickup_branch_id,
                            payment_method, pay_with, delivery_carrier, delivery_mode,
                            delivery_tariff, delivery_price, delivery_days,
                            delivery_city, delivery_point, delivery_point_address,
                            delivery_postcode, delivery_cdek_code, delivery_country,
                            delivery_street, delivery_house, delivery_block, delivery_flat)
        VALUES (:n, :c, 'new', 'site', :total, :dm, :da, :cm,
                :cn, :cp, :br, :pm, :pw, :dc, :dmode, :dt, :dp, :dd,
                :dcity, :dpt, :dpta, :dpost, :dcode, :dcountry,
                :dstreet, :dhouse, :dblock, :dflat)
        RETURNING id
    """),
            {
                "n": number,
                "c": customer["id"],
                "total": total,
                "dm": payload.delivery_method,
                "da": address or None,
                "cm": (payload.comment or "").strip() or None,
                "cn": name,
                "cp": phone,
                "br": branch,
                "pm": payload.payment_method,
                "pw": payload.pay_with if payload.payment_method == "online" else None,
                "dc": ship and ship["carrier"], "dmode": ship and ship["mode"],
                "dt": ship and ship["tariff"], "dp": ship and ship["price"],
                "dd": ship and ship["days"], "dcity": ship and ship["city"],
                "dpt": ship and ship["point"], "dpta": ship and ship["point_address"],
                "dpost": ship and ship["postcode"],
                "dcode": payload.delivery_cdek_code if ship else None,
                # Адрес и по полям — менеджер поправит улицу, не трогая остальное
                **{f"d{k}": (getattr(payload, f"delivery_{k}") or "").strip() or None
                   if payload.delivery_method == "shipping" and payload.delivery_street is not None
                   and payload.delivery_mode != "pvz" else None
                   for k in ("country", "street", "house", "block", "flat")},
            },
        )
    ).scalar_one()

    # Посылки — по одной из каждого филиала, с ценой и сроком своей
    # посылки; деталь знает, в какой посылке едет
    shipment_of: dict[int, int] = {}
    for x in (ship["parcels"] if ship else []):
        sid = (await session.execute(text("""
            INSERT INTO order_shipments (order_id, branch_id, carrier, mode, tariff, price,
                                         days_min, days_max, weight_g, point, address)
            VALUES (:o, :b, :c, :m, :t, :p, :d1, :d2, :w, :pt, :a)
            RETURNING id"""),
            {"o": order_id, "b": x["branch_id"], "c": ship["carrier"], "m": ship["mode"],
             "t": x["tariff"], "p": x["price"], "d1": x["days_min"], "d2": x["days_max"],
             "w": x["weight_g"], "pt": ship["point"], "a": address or None})).scalar_one()
        shipment_of.update({pid: sid for pid in x["part_ids"]})

    for i in items:
        await session.execute(
            text("""
            INSERT INTO order_items (order_id, part_id, price, qty, shipment_id)
            VALUES (:o, :p, :price, :q, :s)
        """),
            {"o": order_id, "p": i["part_id"], "price": i["price"], "q": i["qty"],
             "s": shipment_of.get(i["part_id"])},
        )
        # Списываем штуки. Условие на остаток — защита от гонки: если
        # кто-то секундой раньше забрал последние, обновится ноль строк.
        # Остаток кончился — деталь уходит с витрины
        took = (await session.execute(text("""
            UPDATE parts SET quantity = quantity - :q,
                   status = CASE WHEN quantity - :q = 0
                                 THEN 'reserved'::part_status ELSE status END,
                   updated_at = now()
             WHERE id = :p AND status = 'in_stock' AND quantity >= :q"""),
            {"q": i["qty"], "p": i["part_id"]})).rowcount
        if not took:
            await session.rollback()
            raise HTTPException(409, f"{i['sku']}: столько штук уже нет — "
                                     "обновите корзину.")

    await order_log.log(session, order_id, "created", "Заказ оформлен на сайте",
                        data={"total": total})

    # Из корзины — только у этого покупателя: остаток мог остаться, и
    # у других та же деталь лежит законно
    await session.execute(
        text("""
        DELETE FROM cart_items
         WHERE part_id = ANY(:ids)
           AND (cart_token = coalesce(:t, '') OR customer_id = :c)"""),
        {"ids": [i["part_id"] for i in items], "t": cart_token(request), "c": customer["id"]},
    )
    # Покупатель вошёл через соцсеть и телефона у него не было — запомним
    # тот, что он указал, чтобы в следующий раз не спрашивать. Только
    # если номер ничей: уникальность важнее удобства
    await session.execute(text("""
        UPDATE customers SET phone = :p, name = coalesce(name, :n)
         WHERE id = :c AND phone IS NULL
           AND NOT EXISTS (SELECT 1 FROM customers WHERE phone = :p)"""),
        {"p": phone, "n": name, "c": customer["id"]})
    await session.commit()

    out = {"number": number}
    if online:
        # Заказ уже оформлен: банк не ответил — заказ всё равно есть,
        # оплатить можно со страницы заказа
        try:
            out["redirect_url"] = await start_payment(
                session, {"id": order_id, "number": number, "total": total},
                payload.pay_with, request)
        except payments.PaymentError as e:
            out["payment_error"] = str(e)
    return out


async def shipping_choice(session: AsyncSession, request: Request, customer: dict,
                          payload: "OrderIn", address: str) -> dict:
    """Выбранная доставка службой — с ценой, посчитанной заново здесь.
    Пункт выдачи сверяем со списком службы: выдумать его нельзя."""
    # Роутер доставки сам импортирует shop — поэтому здесь, а не наверху
    from .delivery import quotes_for_cart

    delivery = ship_services
    if payload.delivery_carrier not in delivery.enabled():
        raise HTTPException(422, "Эта служба доставки сейчас недоступна")
    city = (payload.delivery_city or "").strip()
    if len(city) < 2:
        raise HTTPException(422, "Укажите город доставки")
    mode = payload.delivery_mode
    point_addr = None
    if mode == "pvz":
        if not payload.delivery_point:
            raise HTTPException(422, "Выберите пункт выдачи")
        pts = await delivery.points(payload.delivery_carrier,
                                    {"city": city, "cdek_code": payload.delivery_cdek_code})
        hit = next((p for p in pts if p["code"] == payload.delivery_point), None)
        if not hit:
            raise HTTPException(422, "Пункт выдачи не найден — выберите другой")
        # Полный адрес пункта уже содержит город — его и храним, с кодом
        # пункта: по нему менеджер найдёт пункт в кабинете службы
        point_addr = f"{hit['address']} (пункт {hit['code']})"
        address = point_addr
    elif mode == "door" and len(address) < 10:
        raise HTTPException(422, "Адрес доставки: улица, дом, квартира — не короче 10 символов")
    elif mode == "post":
        if not payload.delivery_postcode:
            raise HTTPException(422, "Укажите индекс для Почты России")
        if len(address) < 10:
            raise HTTPException(422, "Адрес для Почты: улица, дом, квартира — не короче 10 символов")
    elif mode not in ("pvz", "door", "post"):
        raise HTTPException(422, "Выберите вариант доставки")

    dest = {"city": city, "cdek_code": payload.delivery_cdek_code,
            "postcode": payload.delivery_postcode,
            "point": payload.delivery_point if payload.delivery_carrier == "yandex" else None}
    got, _ = await quotes_for_cart(session, request, customer, dest)
    o = next((o for o in got["options"]
              if o["carrier"] == payload.delivery_carrier and o["mode"] == mode), None)
    if not o:
        # Служба не берёт одну из посылок — говорим, какую
        why = got["issues"].get(payload.delivery_carrier)
        raise HTTPException(409, why + " — выберите другой вариант" if why else
                            "В этот пункт выдачи служба не доставляет — выберите другой"
                            if mode == "pvz" else
                            "Служба не посчитала доставку — выберите другой вариант")
    return {**o, "city": city, "point": payload.delivery_point if mode == "pvz" else None,
            "point_address": point_addr, "postcode": payload.delivery_postcode,
            "address": address if mode != "pvz" else point_addr}


async def start_payment(session: AsyncSession, order: dict, method: str,
                        request: Request) -> str:
    """Платёж у провайдера и строка в payments → ссылка, куда уйти платить.
    Незакрытый платёж тем же способом не плодим — отдаём его ссылку."""
    m = payments.method(method)
    if not m:
        raise payments.PaymentError("Этот способ оплаты сейчас недоступен")
    kind = m["provider"]
    open_one = (await session.execute(text("""
        SELECT confirmation_url FROM payments
         WHERE order_id = :o AND method = :m AND provider = :pr AND status = 'pending'
           AND confirmation_url IS NOT NULL AND created_at > now() - interval '30 minutes'
         ORDER BY id DESC LIMIT 1"""),
        {"o": order["id"], "m": method, "pr": kind})).scalar()
    if open_one:
        return open_one

    back = f"{settings.base_url}/account/orders/{order['number']}?paid=1"
    order = {**order, **await receipt_of(session, order["id"])}
    # Строка платежа — до похода к провайдеру: её номер нужен Робокассе
    # как номер счёта. Провайдер не ответил — строку убираем
    pay_id = (await session.execute(text("""
        INSERT INTO payments (order_id, method, amount, status, provider)
        VALUES (:o, :m, :a, 'pending', :pr) RETURNING id"""),
        {"o": order["id"], "m": method, "a": order["total"], "pr": kind})).scalar_one()
    await session.commit()
    try:
        ext_id, url = await payments.create(order, method, back, pay_id)
    except payments.PaymentError:
        await session.execute(text("DELETE FROM payments WHERE id = :id"), {"id": pay_id})
        await session.commit()
        raise
    if ext_id == "rk-preview":
        # Робокасса ещё не подключена — платежа не было, в истории
        # заказа ему нечего делать
        await session.execute(text("DELETE FROM payments WHERE id = :id"), {"id": pay_id})
        await session.commit()
        return f"/pay/robokassa/preview/{order['number']}"
    await session.execute(text("""
        UPDATE payments SET external_id = :x, confirmation_url = :u WHERE id = :id"""),
        {"x": ext_id, "u": url, "id": pay_id})
    await session.commit()
    return url


async def receipt_of(session: AsyncSession, order_id: int) -> dict:
    """Позиции чека и куда его прислать. Одни и те же строки уходят в
    кассовый чек ЮKassa и показываются на странице чека в кабинете.
    → {items: [{name, sku, price, qty, subject}], email, phone}."""
    items = [dict(r._mapping) for r in await session.execute(text("""
        SELECT p.name, p.sku, oi.price, oi.qty FROM order_items oi JOIN parts p ON p.id = oi.part_id
         WHERE oi.order_id = :o ORDER BY oi.id"""), {"o": order_id})]
    out = {"items": items, "email": None, "phone": None}
    contact = (await session.execute(text("""
        SELECT coalesce(o.contact_email, c.email) AS email,
               coalesce(o.contact_phone, c.phone) AS phone,
               o.delivery_price, o.delivery_carrier
          FROM orders o LEFT JOIN customers c ON c.id = o.customer_id
         WHERE o.id = :o"""), {"o": order_id})).first()
    if not contact:
        return out
    out.update(email=contact.email, phone=contact.phone)
    # Доставка входит в сумму заказа — значит, и в чек: иначе сумма
    # позиций не сойдётся с платежом, и ЮKassa его отклонит. Строка
    # на каждую посылку: «Доставка СДЭК из Перми»
    parcels = [s for s in await shipments_of(session, [order_id]) if s["price"]]
    if not parcels and contact.delivery_price:
        parcels = [{"carrier": contact.delivery_carrier, "price": contact.delivery_price}]
    for s in parcels:
        name = ship_services.CARRIERS.get(s["carrier"], "")
        frm = s.get("from_city", "") if len(parcels) > 1 else ""
        items.append({"name": " ".join(x for x in ("Доставка", name, frm) if x),
                      "sku": "", "price": s["price"], "qty": 1, "subject": "service"})
    return out


async def next_order_number(session: AsyncSession) -> int:
    return (await session.execute(text("SELECT nextval('order_number_seq')"))).scalar_one()


# ------------------------------------------------------------------
# Личный кабинет
# ------------------------------------------------------------------

SHIPMENT_LABELS = {"assembling": "собирается", "sent": "отправлена",
                   "delivered": "доставлена", "cancelled": "отменена"}


async def shipments_of(session: AsyncSession, order_ids: list[int]) -> list[dict]:
    """Посылки заказов — откуда, чем, за сколько, где сейчас."""
    rows = await session.execute(text("""
        SELECT s.id, s.order_id, s.branch_id, b.city, b.name AS branch_name,
               s.carrier, s.mode, s.tariff, s.price, s.days_min, s.days_max, s.weight_g,
               s.track_number, s.status, s.sent_at, s.delivered_at, s.point, s.address
          FROM order_shipments s LEFT JOIN branches b ON b.id = s.branch_id
         WHERE s.order_id = ANY(:ids) ORDER BY s.id"""), {"ids": order_ids})
    out = [{**dict(r._mapping), "track_url": ship_services.track_url(r.carrier, r.track_number),
            "days": ship_services._span(r.days_min, r.days_max)["days"]} for r in rows]
    by_order: dict[int, list] = {}
    for s in out:
        by_order.setdefault(s["order_id"], []).append(s)
    for group in by_order.values():
        labels = ship_services.parcel_labels(
            [{"city": s["city"], "name": s["branch_name"]} for s in group])
        for s, label in zip(group, labels):
            s["from_city"] = label
    return out



async def orders_of(session: AsyncSession, customer_id: int, number: str | None = None):
    """Заказы с составом. Цена берётся из order_items, а не из parts:
    деталь могла подорожать после покупки, в истории должно остаться
    то, что человек заплатил."""
    rows = await session.execute(
        text("""
        SELECT o.id, o.number, o.status::text AS status, o.total, o.created_at,
               o.paid_at, o.delivery_method, o.delivery_address, o.comment,
               o.contact_name, o.contact_phone, o.payment_method, o.pay_with,
               o.delivery_carrier, o.delivery_mode, o.delivery_price, o.delivery_days,
               o.delivery_city, o.delivery_point_address, o.delivery_postcode,
               o.delivery_point, o.delivery_cdek_code, o.delivery_country, o.delivery_street,
               o.delivery_house, o.delivery_block, o.delivery_flat, o.pickup_branch_id,
               (SELECT br.city || ', ' || br.name FROM branches br
                 WHERE br.id = o.pickup_branch_id) AS pickup_branch
          FROM orders o
         WHERE o.customer_id = :c
           AND (CAST(:n AS text) IS NULL OR o.number = CAST(:n AS text))
         ORDER BY o.created_at DESC
    """),
        {"c": customer_id, "n": number},
    )
    orders = [dict(r._mapping) for r in rows]
    if not orders:
        return []

    items = await session.execute(
        text("""
        SELECT oi.id AS item_id, oi.order_id, oi.price, oi.qty, oi.price * oi.qty AS sum,
               oi.shipment_id, oi.source, p.quantity AS stock,
               (SELECT br.city FROM branches br WHERE br.id = p.branch_id) AS city,
               (SELECT br.name FROM branches br WHERE br.id = p.branch_id) AS branch_name,
               p.sku, p.name, p.status::text AS status,
               p.condition::text AS condition,
               (SELECT coalesce(ph.thumb, ph.path) FROM part_photos ph
                 WHERE ph.part_id = p.id ORDER BY sort_order LIMIT 1) AS photo,
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
        r["label"] = SHIPMENT_LABELS.get(r["status"], r["status"])
        ships.setdefault(r["order_id"], []).append(r)
    for o in orders:
        # Не items: в шаблоне order.items достался бы метод словаря,
        # Jinja сначала пробует атрибут и только потом ключ
        o["lines"] = by_order.get(o["id"], [])
        o["shipments"] = ships.get(o["id"], [])
        for sh in o["shipments"]:
            sh["lines"] = [i for i in o["lines"] if i["shipment_id"] == sh["id"]]
        o["label"] = ORDER_LABELS.get(o["status"], o["status"])
    return orders


@router.get("/account", response_class=HTMLResponse)
async def account(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict = Depends(ca.current_customer),
):
    orders = await orders_of(session, customer["id"])

    # История поиска: по VIN и по строке — это разные таблицы,
    # но для человека это один список «что я искал»
    vins = [
        dict(r._mapping)
        for r in await session.execute(
            text("""
        SELECT q.vin, q.resolution, q.results_count, q.created_at,
               b.name AS brand, m.name AS model, g.name AS generation
          FROM vin_queries q
          LEFT JOIN generations g ON g.id = q.generation_id
          LEFT JOIN models m      ON m.id = g.model_id
          LEFT JOIN brands b      ON b.id = m.brand_id
         WHERE q.customer_id = :c
         ORDER BY q.created_at DESC LIMIT 30
    """),
            {"c": customer["id"]},
        )
    ]
    searches = [
        dict(r._mapping)
        for r in await session.execute(
            text("""
        SELECT query, results_count, created_at
          FROM search_queries
         WHERE customer_id = :c
         ORDER BY created_at DESC LIMIT 30
    """),
            {"c": customer["id"]},
        )
    ]

    return templates.TemplateResponse(
        "shop/account.html",
        {
            "request": request,
            "user": None,
            "customer": customer,
            "orders": orders,
            "vins": vins,
            "searches": searches,
            "spent": sum(o["total"] for o in orders if o["status"] in
                         ("paid", "shipped", "completed")),
        },
    )


@router.delete("/api/account/searches", status_code=204)
async def clear_searches(
    session: AsyncSession = Depends(get_session),
    customer: dict = Depends(ca.current_customer),
):
    """Очистить историю поиска в кабинете.

    Строки не удаляем, а обезличиваем: по ним считается отчёт
    о несостоявшемся спросе — что искали и не нашли. Это основание
    покупать машины на разбор, и терять его из-за того, что человек
    прибрался у себя в кабинете, нельзя. Связь с покупателем при этом
    рвётся полностью: в кабинете не остаётся ничего, и обратно
    сопоставить записи не с чем.
    """
    for table in ("search_queries", "vin_queries"):
        await session.execute(
            text(f"UPDATE {table} SET customer_id = NULL WHERE customer_id = :c"),
            {"c": customer["id"]},
        )
    await session.commit()
    return Response(status_code=204)


@router.get("/account/orders/{number}", response_class=HTMLResponse)
async def order_page(
    number: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict = Depends(ca.current_customer),
):
    orders = await orders_of(session, customer["id"], number)
    if not orders:
        raise HTTPException(404, "Заказ не найден")

    order = orders[0]
    # Вернулись со страницы банка — уведомление могло ещё не дойти,
    # сверяем сами. Статус мог смениться — перечитываем заказ
    if order["status"] in ("new", "confirmed"):
        await payments.refresh(session, order["id"])
        order = (await orders_of(session, customer["id"], number))[0]

    attempts = [
        {**dict(r._mapping),
         "method_label": payments.title_of(r.method),
         "status_label": PAYMENT_STATUS.get(r.status, r.status)}
        for r in await session.execute(
            text("""
        SELECT method, amount, status, created_at, paid_at
          FROM payments WHERE order_id = :o ORDER BY id DESC
    """),
            {"o": order["id"]},
        )
    ]

    return templates.TemplateResponse(
        "shop/order.html",
        {
            "request": request,
            "user": None,
            "customer": customer,
            "order": order,
            "payments": attempts,
            "methods": payments.methods(),
            "payment_label": PAYMENT_LABELS.get(order["payment_method"] or ""),
            "returned": request.query_params.get("paid") == "1",
            # Платить есть смысл, пока заказ не оплачен и не отменён
            "payable": order["status"] in ("new", "confirmed"),
            # Править заказ сам покупатель может, пока он новый и не оплачен
            # (app/routers/account_orders.py)
            "editable": order["status"] == "new" and not order["paid_at"],
            "branches": [dict(r._mapping) for r in await session.execute(text("""
                SELECT id, city || ', ' || name AS label FROM branches
                 WHERE is_active ORDER BY sort_order, city, name"""))]
                        if order["delivery_method"] == "pickup" else [],
        },
    )


class PayIn(BaseModel):
    method: str = Field(max_length=32)


@router.post("/api/orders/{number}/pay")
async def pay(
    number: str,
    payload: PayIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict = Depends(ca.current_customer),
):
    """Оплатить оформленный заказ — со страницы заказа: при оформлении
    выбрали «при получении» и передумали, или банк в тот раз не ответил."""
    order = (
        await session.execute(
            text("""
        SELECT id, number, status::text AS status, total
          FROM orders WHERE number = :n AND customer_id = :c
    """),
            {"n": number, "c": customer["id"]},
        )
    ).first()

    if not order:
        raise HTTPException(404, "Заказ не найден")
    if order.status not in ("new", "confirmed"):
        raise HTTPException(409, "Этот заказ уже оплачен или отменён")
    if not payments.method(payload.method):
        raise HTTPException(
            501,
            "Онлайн-оплата пока не подключена. Менеджер примет оплату "
            "при получении или выставит счёт.",
        )
    # Способ, которым начали платить, — способ оплаты заказа
    # (account_orders импортирует shop — поэтому здесь, а не наверху)
    from .account_orders import switch_payment
    full = (await session.execute(text("SELECT * FROM orders WHERE id = :o FOR UPDATE"),
                                  {"o": order.id})).first()
    await switch_payment(session, full, payload.method)
    await session.commit()
    try:
        url = await start_payment(session, dict(order._mapping), payload.method, request)
    except payments.PaymentError as e:
        raise HTTPException(502, str(e)) from e
    return {"redirect_url": url}


@router.post("/api/payments/yookassa", status_code=200)
async def yookassa_notify(request: Request, session: AsyncSession = Depends(get_session)):
    """Уведомление ЮKassa. Телу не верим — берём из него только id
    платежа и спрашиваем статус у самой ЮKassa."""
    if not payments.yookassa_ready():
        raise HTTPException(404)
    try:
        ext_id = str((await request.json())["object"]["id"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400) from None
    pid = (await session.execute(text("""
        SELECT id FROM payments WHERE provider = 'yookassa' AND external_id = :x"""),
        {"x": ext_id})).scalar()
    if pid:
        await payments.settle(session, pid, await payments.remote_status(ext_id))
    return {"ok": True}


# ------------------------------------------------------------------
# Учебная оплата — только при PAYMENT_DEMO=true
# ------------------------------------------------------------------


async def demo_payment(session: AsyncSession, ext_id: str, customer: dict):
    if not settings.payment_demo:
        raise HTTPException(404)
    row = (await session.execute(text("""
        SELECT pm.id, pm.amount, pm.status, pm.method, o.number
          FROM payments pm JOIN orders o ON o.id = pm.order_id
         WHERE pm.provider = 'demo' AND pm.external_id = :x AND o.customer_id = :c"""),
        {"x": ext_id, "c": customer["id"]})).first()
    if not row:
        raise HTTPException(404, "Платёж не найден")
    return row


@router.get("/pay/demo/{ext_id}", response_class=HTMLResponse)
async def demo_page(
    ext_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict = Depends(ca.current_customer),
):
    row = await demo_payment(session, ext_id, customer)
    return templates.TemplateResponse("shop/pay_demo.html", {
        "request": request, "user": None, "customer": customer, "p": row,
        "method": payments.title_of(row.method),
    })


class DemoResult(BaseModel):
    result: str = Field(pattern="^(paid|failed)$")


@router.post("/pay/demo/{ext_id}")
async def demo_finish(
    ext_id: str,
    payload: DemoResult,
    session: AsyncSession = Depends(get_session),
    customer: dict = Depends(ca.current_customer),
):
    row = await demo_payment(session, ext_id, customer)
    await payments.settle(session, row.id, payload.result)
    return {"redirect_url": f"/account/orders/{row.number}?paid=1"}


# ------------------------------------------------------------------
# Робокасса
# ------------------------------------------------------------------


@router.api_route("/api/payments/robokassa/result", methods=["GET", "POST"])
async def robokassa_result(request: Request, session: AsyncSession = Depends(get_session)):
    """ResultURL: Робокасса сообщает об оплате. Подпись — паролем №2;
    сумма должна совпасть с платежом. Ответ «OK<номер>» — так Робокасса
    понимает, что уведомление принято, и перестаёт его повторять."""
    if not payments.robokassa_ready():
        raise HTTPException(404)
    data = dict(request.query_params)
    if request.method == "POST":
        data.update({k: v for k, v in (await request.form()).items()})
    out_sum, inv_id = data.get("OutSum", ""), data.get("InvId", "")
    if not payments.robokassa_result_ok(out_sum, inv_id, data.get("SignatureValue", "")):
        return Response("bad sign", status_code=400, media_type="text/plain")
    row = (await session.execute(text("""
        SELECT id, amount FROM payments
         WHERE provider = 'robokassa' AND id = :id"""),
        {"id": int(inv_id) if inv_id.isdigit() else 0})).first()
    if not row or payments.robokassa_sum(row.amount) != payments.robokassa_sum(out_sum):
        return Response("unknown invoice", status_code=400, media_type="text/plain")
    await payments.settle(session, row.id, "paid")
    return Response(f"OK{inv_id}", media_type="text/plain")


async def robokassa_order_number(session: AsyncSession, inv_id: str) -> str | None:
    if not inv_id.isdigit():
        return None
    return (await session.execute(text("""
        SELECT o.number FROM payments p JOIN orders o ON o.id = p.order_id
         WHERE p.provider = 'robokassa' AND p.id = :id"""), {"id": int(inv_id)})).scalar()


@router.get("/pay/robokassa/success")
async def robokassa_success(InvId: str = "", session: AsyncSession = Depends(get_session)):
    """Человек вернулся после оплаты. Статус здесь не меняем — его
    отмечает ResultURL; страница заказа покажет «проверяем оплату»."""
    number = await robokassa_order_number(session, InvId)
    return RedirectResponse(f"/account/orders/{number}?paid=1" if number else "/account",
                            status_code=303)


@router.get("/pay/robokassa/fail")
async def robokassa_fail(InvId: str = "", session: AsyncSession = Depends(get_session)):
    """Человек отказался от оплаты или она не прошла."""
    number = await robokassa_order_number(session, InvId)
    if number:
        await payments.settle(session, int(InvId), "failed")
    return RedirectResponse(f"/account/orders/{number}?paid=1" if number else "/account",
                            status_code=303)


@router.get("/pay/robokassa/preview/{number}", response_class=HTMLResponse)
async def robokassa_preview(
    number: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict = Depends(ca.current_customer),
):
    """Робокасса выбрана, а ключей ещё нет: заказ оформлен и закреплён
    за покупателем, оплатить — при получении или позже со страницы заказа."""
    row = (await session.execute(text("""
        SELECT number, total AS amount FROM orders WHERE number = :n AND customer_id = :c"""),
        {"n": number, "c": customer["id"]})).first()
    if not row:
        raise HTTPException(404, "Заказ не найден")
    return templates.TemplateResponse("shop/pay_robokassa_preview.html", {
        "request": request, "user": None, "customer": customer, "p": row,
    })

