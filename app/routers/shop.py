"""Покупательская часть: корзина, заказ, оплата, личный кабинет.

Деталь штучная — это определяет здесь почти всё. Количества в корзине
нет, две одинаковые позиции невозможны, а заказ занимает деталь
физически: как только он оформлен, деталь уходит в reserved и с витрины
пропадает. Иначе двое купят один и тот же бампер.
"""

import secrets
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import customer_auth as ca
from .. import mailer, payments
from ..config import settings
from ..database import get_session
from ..templating import templates

router = APIRouter(tags=["shop"])

CART_COOKIE = "razbor_cart"

NAME_RE = ca.NAME_RE

# Способы онлайн-оплаты — из app/payments.py: без ключей ЮKassa список
# пуст, и заказ оплачивается при получении, а менеджер отмечает оплату
# в бэкенде
PAYMENT_LABELS = {"online": "онлайн, картой или СБП", "on_receipt": "при получении"}
PAYMENT_STATUS = {"pending": "ожидает оплаты", "paid": "оплачен", "failed": "не прошёл"}

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

    rows = await session.execute(
        text("""
        SELECT ci.part_id, p.sku, p.name, p.price, p.status::text AS status,
               p.condition::text AS condition,
               (SELECT coalesce(ph.thumb, ph.path) FROM part_photos ph
                 WHERE ph.part_id = p.id ORDER BY sort_order LIMIT 1) AS photo,
               (SELECT br.city || ', ' || br.name FROM branches br
                 WHERE br.id = p.branch_id) AS branch,
               p.branch_id,
               ci.added_at
          FROM cart_items ci
          JOIN parts p ON p.id = ci.part_id
         WHERE ci.cart_token = coalesce(:t, '')
            OR (ci.customer_id IS NOT NULL AND ci.customer_id = :c)
         ORDER BY ci.added_at
    """),
        {"t": token, "c": customer["id"] if customer else None},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/cart", response_class=HTMLResponse)
async def cart_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict | None = Depends(ca.optional_customer),
):
    items = await cart_rows(session, cart_token(request), customer)
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
            # Деталь могли продать, пока она лежала в корзине
            "gone": [i for i in items if i["status"] != "in_stock"],
            "no_price": [i for i in items if i["price"] is None],
            "total": sum(i["price"] for i in items
                         if i["price"] is not None and i["status"] == "in_stock"),
        },
    )


class CartAdd(BaseModel):
    sku: str = Field(max_length=32)


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
        SELECT id, status::text AS status, published
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
    await session.execute(
        text("""
        INSERT INTO cart_items (cart_token, customer_id, part_id)
        VALUES (:t, :c, :p)
        ON CONFLICT (cart_token, part_id) DO NOTHING
    """),
        {"t": token, "c": customer["id"] if customer else None, "p": part.id},
    )
    await session.commit()

    count = len(await cart_rows(session, token, customer))
    # Кука ставится на ответ, а не заранее: корзины может и не быть
    response.set_cookie(CART_COOKIE, token, max_age=30 * 86400,
                        httponly=True, samesite="lax", path="/")
    return {"count": count}


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
        "cart": len(items),
    }


@router.get("/api/cart")
async def cart_count(
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict | None = Depends(ca.optional_customer),
):
    """Счётчик для шапки."""
    items = await cart_rows(session, cart_token(request), customer)
    return {"count": len(items)}


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
    branch = None
    if payload.delivery_method == "pickup":
        branch = (await session.execute(
            text("SELECT id FROM branches WHERE id = :b AND is_active"),
            {"b": payload.pickup_branch_id})).scalar()
        if not branch:
            raise HTTPException(422, "Выберите филиал для самовывоза")
        address = ""
    elif len(address) < 10:
        raise HTTPException(422, "Адрес доставки: город, улица, дом — не короче 10 символов")
    if payload.payment_method not in PAYMENT_LABELS:
        raise HTTPException(422, "Выберите способ оплаты")
    online = payload.payment_method == "online"
    if online and not any(m["code"] == payload.pay_with for m in payments.methods()):
        raise HTTPException(422, "Онлайн-оплата сейчас недоступна — выберите оплату при получении")
    if not payload.agree:
        raise HTTPException(422, "Нужно согласие на обработку персональных данных")

    total = sum(i["price"] for i in items)
    number = f"{date.today().year}-{await next_order_number(session):06d}"

    order_id = (
        await session.execute(
            text("""
        INSERT INTO orders (number, customer_id, status, source, total,
                            delivery_method, delivery_address, comment,
                            contact_name, contact_phone, pickup_branch_id,
                            payment_method)
        VALUES (:n, :c, 'new', 'site', :total, :dm, :da, :cm,
                :cn, :cp, :br, :pm)
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
            },
        )
    ).scalar_one()

    for i in items:
        await session.execute(
            text("""
            INSERT INTO order_items (order_id, part_id, price)
            VALUES (:o, :p, :price)
        """),
            {"o": order_id, "p": i["part_id"], "price": i["price"]},
        )

    # Деталь занята. Условие на status — защита от гонки: если кто-то
    # успел оформить её секундой раньше, обновится ноль строк
    reserved = (
        await session.execute(
            text("""
        UPDATE parts SET status = 'reserved', updated_at = now()
         WHERE id = ANY(:ids) AND status = 'in_stock'
    """),
            {"ids": [i["part_id"] for i in items]},
        )
    ).rowcount

    if reserved != len(items):
        await session.rollback()
        raise HTTPException(409, "Деталь только что купили. Обновите корзину.")

    await session.execute(
        text("DELETE FROM cart_items WHERE part_id = ANY(:ids)"),
        {"ids": [i["part_id"] for i in items]},
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


async def start_payment(session: AsyncSession, order: dict, method: str,
                        request: Request) -> str:
    """Платёж у провайдера и строка в payments → ссылка, куда уйти платить.
    Незакрытый платёж тем же способом не плодим — отдаём его ссылку."""
    kind = payments.provider()
    open_one = (await session.execute(text("""
        SELECT confirmation_url FROM payments
         WHERE order_id = :o AND method = :m AND provider = :pr AND status = 'pending'
           AND confirmation_url IS NOT NULL AND created_at > now() - interval '30 minutes'
         ORDER BY id DESC LIMIT 1"""),
        {"o": order["id"], "m": method, "pr": kind})).scalar()
    if open_one:
        return open_one

    back = f"{settings.base_url}/account/orders/{order['number']}?paid=1"
    ext_id, url = await payments.create(order, method, back)
    await session.execute(text("""
        INSERT INTO payments (order_id, method, amount, status, external_id,
                              confirmation_url, provider)
        VALUES (:o, :m, :a, 'pending', :x, :u, :pr)"""),
        {"o": order["id"], "m": method, "a": order["total"], "x": ext_id,
         "u": url, "pr": kind})
    await session.commit()
    return url


async def next_order_number(session: AsyncSession) -> int:
    return (await session.execute(text("SELECT nextval('order_number_seq')"))).scalar_one()


# ------------------------------------------------------------------
# Личный кабинет
# ------------------------------------------------------------------


async def orders_of(session: AsyncSession, customer_id: int, number: str | None = None):
    """Заказы с составом. Цена берётся из order_items, а не из parts:
    деталь могла подорожать после покупки, в истории должно остаться
    то, что человек заплатил."""
    rows = await session.execute(
        text("""
        SELECT o.id, o.number, o.status::text AS status, o.total, o.created_at,
               o.paid_at, o.delivery_method, o.delivery_address, o.comment,
               o.contact_name, o.contact_phone, o.payment_method,
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
        SELECT oi.order_id, oi.price, p.sku, p.name, p.status::text AS status,
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
    for o in orders:
        # Не items: в шаблоне order.items достался бы метод словаря,
        # Jinja сначала пробует атрибут и только потом ключ
        o["lines"] = by_order.get(o["id"], [])
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
         "method_label": next((m["title"] for m in payments.METHODS
                               if m["code"] == r.method), r.method),
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
    if not any(m["code"] == payload.method for m in payments.methods()):
        raise HTTPException(
            501,
            "Онлайн-оплата пока не подключена. Менеджер примет оплату "
            "при получении или выставит счёт.",
        )
    try:
        url = await start_payment(session, dict(order._mapping), payload.method, request)
    except payments.PaymentError as e:
        raise HTTPException(502, str(e)) from e
    return {"redirect_url": url}


@router.post("/api/payments/yookassa", status_code=200)
async def yookassa_notify(request: Request, session: AsyncSession = Depends(get_session)):
    """Уведомление ЮKassa. Телу не верим — берём из него только id
    платежа и спрашиваем статус у самой ЮKassa."""
    if payments.provider() != "yookassa":
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
    if payments.provider() != "demo":
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
        "method": next((m["title"] for m in payments.METHODS if m["code"] == row.method),
                       row.method),
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
