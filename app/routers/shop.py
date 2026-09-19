"""Покупательская часть: корзина, заказ, оплата, личный кабинет.

Деталь штучная — это определяет здесь почти всё. Количества в корзине
нет, две одинаковые позиции невозможны, а заказ занимает деталь
физически: как только он оформлен, деталь уходит в reserved и с витрины
пропадает. Иначе двое купят один и тот же бампер.
"""

import secrets
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import customer_auth as ca
from ..database import get_session
from ..templating import templates

router = APIRouter(tags=["shop"])

CART_COOKIE = "razbor_cart"

# Способы оплаты. Пусто — значит онлайн-оплата не подключена: заказ
# оформляется, но платит человек при получении, а менеджер отмечает
# оплату в бэкенде. Подключение провайдера = строка здесь и обработчик
# в pay(); схема и шаблоны уже рассчитаны на это.
PAYMENT_METHODS: list[dict] = []

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
    return templates.TemplateResponse(
        "shop/cart.html",
        {
            "request": request,
            "user": None,
            "customer": customer,
            "items": items,
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
        "name": (customer or {}).get("name") or (customer or {}).get("phone"),
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
    return templates.TemplateResponse(
        "shop/login.html",
        {"request": request, "user": None, "customer": None,
         "next": next, "error": error},
    )


class Credentials(BaseModel):
    phone: str = Field(max_length=40)
    password: str = Field(min_length=6, max_length=200)
    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=200)


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
    phone = ca.normalize_phone(payload.phone)
    if not phone:
        raise HTTPException(422, "Проверьте номер телефона")

    customer = await ca.register(session, phone, payload.password,
                                 payload.name, payload.email)
    await adopt_cart(session, cart_token(request), customer["id"])
    ca.issue(response, customer["id"])
    return {"ok": True}


@router.post("/api/account/login")
async def login(
    payload: Credentials,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    phone = ca.normalize_phone(payload.phone)
    if not phone:
        raise HTTPException(422, "Проверьте номер телефона")

    customer = await ca.authenticate(session, phone, payload.password)
    if not customer:
        raise HTTPException(401, "Неверный телефон или пароль")

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
    delivery_method: str = Field(default="pickup", max_length=32)
    delivery_address: str | None = Field(default=None, max_length=500)
    comment: str | None = Field(default=None, max_length=1000)


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

    if payload.delivery_method == "shipping" and not (payload.delivery_address or "").strip():
        raise HTTPException(422, "Укажите адрес доставки")

    total = sum(i["price"] for i in items)
    number = f"{date.today().year}-{await next_order_number(session):06d}"

    order_id = (
        await session.execute(
            text("""
        INSERT INTO orders (number, customer_id, status, source, total,
                            delivery_method, delivery_address, comment)
        VALUES (:n, :c, 'new', 'site', :total, :dm, :da, :cm)
        RETURNING id
    """),
            {
                "n": number,
                "c": customer["id"],
                "total": total,
                "dm": payload.delivery_method,
                "da": (payload.delivery_address or "").strip() or None,
                "cm": (payload.comment or "").strip() or None,
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
    await session.commit()
    return {"number": number}


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
               o.paid_at, o.delivery_method, o.delivery_address, o.comment
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
    payments = [
        dict(r._mapping)
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
            "payments": payments,
            "methods": PAYMENT_METHODS,
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
    session: AsyncSession = Depends(get_session),
    customer: dict = Depends(ca.current_customer),
):
    """Единственное место, куда встраивается провайдер.

    Сейчас список способов пуст, поэтому любая попытка честно отвечает,
    что онлайн-оплата не подключена. Когда способ появится, здесь
    создаётся платёж со статусом pending и возвращается ссылка на банк;
    таблица payments и эта ручка меняться не будут.
    """
    order = (
        await session.execute(
            text("""
        SELECT id, status::text AS status, total
          FROM orders WHERE number = :n AND customer_id = :c
    """),
            {"n": number, "c": customer["id"]},
        )
    ).first()

    if not order:
        raise HTTPException(404, "Заказ не найден")
    if order.status not in ("new", "confirmed"):
        raise HTTPException(409, "Этот заказ уже оплачен или отменён")

    if not any(m["code"] == payload.method for m in PAYMENT_METHODS):
        raise HTTPException(
            501,
            "Онлайн-оплата пока не подключена. Менеджер примет оплату "
            "при получении или выставит счёт.",
        )

    # Сюда встанет вызов провайдера и redirect_url в ответе
    raise HTTPException(501, "Способ оплаты не настроен")
