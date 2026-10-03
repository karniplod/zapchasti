"""Личный кабинет покупателя: бонусы и скидки, профиль, адреса, купленное.

Заказы — app/routers/shop.py (список, страница заказа) и
app/routers/account_orders.py (правка, оплата, чек). Правила баллов и
скидок — app/loyalty.py.
"""

import secrets
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import address
from .. import customer_auth as ca
from .. import loyalty, mailer
from ..auth import hash_password, verify_password
from ..database import get_session
from ..templating import templates
from ..validation import address as addr_rules
from ..validation import people, require
from .shop import CART_COOKIE, cart_rows, cart_token

router = APIRouter(tags=["cabinet"])


# ------------------------------------------------------------------
# Бонусы и скидки при оформлении
# ------------------------------------------------------------------


@router.get("/api/account/loyalty")
async def account_loyalty(session: AsyncSession = Depends(get_session),
                          customer: dict = Depends(ca.current_customer)):
    """Баланс баллов и персональная скидка — для оформления заказа."""
    disc = await loyalty.best_discount(session, customer, Decimal(100))
    return {"balance": await loyalty.balance(session, customer["id"]),
            "personal_discount": str(disc["personal"]),
            "accrual_percent": loyalty.ACCRUAL_PERCENT,
            "spend_limit_percent": loyalty.SPEND_LIMIT_PERCENT}


class PromoIn(BaseModel):
    code: str = Field(default="", max_length=40)


@router.post("/api/cart/promo")
async def cart_promo(payload: PromoIn, request: Request,
                     session: AsyncSession = Depends(get_session),
                     customer: dict | None = Depends(ca.optional_customer)):
    """Скидка по корзине — с промокодом или без: оформление показывает
    её до отправки заказа. Сервер при оформлении считает заново."""
    items = await cart_rows(session, cart_token(request), customer)
    goods = sum(((i["price"] or 0) * i["take"] for i in items), Decimal(0))
    d = await loyalty.best_discount(session, customer, goods, payload.code)
    cap = loyalty.bonus_cap(goods - d["amount"])
    bal = await loyalty.balance(session, customer["id"]) if customer else 0
    return {
        "goods": str(goods), "discount": str(d["amount"]), "source": d["source"],
        "kind": d["kind"], "value": str(d["value"]) if d["value"] is not None else None,
        "code": payload.code.strip().upper() if d["source"] == "promo" else None,
        "error": d["promo_error"], "note": d["promo_note"],
        "bonus_balance": bal, "bonus_max": min(cap, bal),
    }


def page(request: Request, name: str, customer: dict, section: str, balance: int, **ctx):
    return templates.TemplateResponse(name, {"request": request, "user": None, "customer": customer,
                                             "section": section, "bonus_balance": balance, **ctx})


# ------------------------------------------------------------------
# Повторить заказ
# ------------------------------------------------------------------
# Детали с разборки обычно штучные: из прошлого заказа в продаже может
# остаться не всё. Кладём в корзину то, что есть, и говорим, чего нет.


@router.post("/api/account/orders/{number}/repeat")
async def repeat_order(number: str, request: Request, response: Response,
                       session: AsyncSession = Depends(get_session),
                       customer: dict = Depends(ca.current_customer)):
    rows = (await session.execute(text("""
        SELECT p.id, p.sku, p.name, p.status::text AS status, p.quantity, p.price, oi.qty
          FROM order_items oi JOIN orders o ON o.id = oi.order_id JOIN parts p ON p.id = oi.part_id
         WHERE o.number = :n AND o.customer_id = :c ORDER BY oi.id"""),
        {"n": number, "c": customer["id"]})).all()
    if not rows:
        raise HTTPException(404, "Заказ не найден")
    token = cart_token(request) or secrets.token_urlsafe(24)
    added, missing = 0, []
    for r in rows:
        take = min(r.qty, r.quantity or 0) if r.status == "in_stock" and r.price else 0
        if not take:
            missing.append(r.name)
            continue
        await session.execute(text("""
            INSERT INTO cart_items (cart_token, customer_id, part_id, qty)
            VALUES (:t, :c, :p, :q)
            ON CONFLICT (cart_token, part_id) DO UPDATE SET qty = greatest(cart_items.qty, EXCLUDED.qty)"""),
            {"t": token, "c": customer["id"], "p": r.id, "q": take})
        added += 1
    await session.commit()
    response.set_cookie(CART_COOKIE, token, max_age=30 * 86400, httponly=True, samesite="lax", path="/")
    return {"added": added, "missing": missing}


# ------------------------------------------------------------------
# История поиска
# ------------------------------------------------------------------


@router.get("/account/searches", response_class=HTMLResponse)
async def searches_page(request: Request, session: AsyncSession = Depends(get_session),
                        customer: dict = Depends(ca.current_customer)):
    # По VIN и по строке — разные таблицы, но для человека это один
    # список «что я искал»
    vins = [dict(r._mapping) for r in await session.execute(text("""
        SELECT q.vin, q.resolution, q.results_count, q.created_at,
               b.name AS brand, m.name AS model, g.name AS generation
          FROM vin_queries q
          LEFT JOIN generations g ON g.id = q.generation_id
          LEFT JOIN models m      ON m.id = g.model_id
          LEFT JOIN brands b      ON b.id = m.brand_id
         WHERE q.customer_id = :c ORDER BY q.created_at DESC LIMIT 30"""), {"c": customer["id"]})]
    searches = [dict(r._mapping) for r in await session.execute(text("""
        SELECT query, results_count, created_at FROM search_queries
         WHERE customer_id = :c ORDER BY created_at DESC LIMIT 30"""), {"c": customer["id"]})]
    return page(request, "shop/account_searches.html", customer, "searches",
                await loyalty.balance(session, customer["id"]), vins=vins, searches=searches)


# ------------------------------------------------------------------
# Купленные товары
# ------------------------------------------------------------------


@router.get("/account/purchased", response_class=HTMLResponse)
async def purchased_page(request: Request, session: AsyncSession = Depends(get_session),
                         customer: dict = Depends(ca.current_customer)):
    """Всё, что человек купил: найти снова, купить ещё, подобрать похожее."""
    rows = [dict(r._mapping) for r in await session.execute(text("""
        SELECT p.id AS part_id, p.sku, p.name, p.status::text AS status, p.quantity, p.price AS price_now,
               oi.price, oi.qty, o.number, o.created_at, o.status::text AS order_status,
               concat_ws(' ', b.name, m.name, d.year) AS car,
               (SELECT coalesce(ph.thumb, ph.path) FROM part_photos ph
                 WHERE ph.part_id = p.id ORDER BY sort_order LIMIT 1) AS photo
          FROM order_items oi JOIN orders o ON o.id = oi.order_id JOIN parts p ON p.id = oi.part_id
          LEFT JOIN donors d ON d.id = p.donor_id
          LEFT JOIN generations g ON g.id = d.generation_id
          LEFT JOIN models m ON m.id = g.model_id
          LEFT JOIN brands b ON b.id = m.brand_id
         WHERE o.customer_id = :c AND o.status IN ('paid', 'shipped', 'completed')
         ORDER BY o.created_at DESC, oi.id"""), {"c": customer["id"]})]
    for r in rows:
        r["available"] = r["status"] == "in_stock" and (r["quantity"] or 0) > 0 and r["price_now"] is not None
    return page(request, "shop/account_purchased.html", customer, "purchased",
                await loyalty.balance(session, customer["id"]), items=rows)


# ------------------------------------------------------------------
# Бонусы и скидки
# ------------------------------------------------------------------

LEDGER_KINDS = {"accrual": "Начислено за заказ", "spend": "Оплата заказа", "refund": "Возврат баллов",
                "revoke": "Снято", "manual": "От магазина"}


@router.get("/account/bonus", response_class=HTMLResponse)
async def bonus_page(request: Request, session: AsyncSession = Depends(get_session),
                     customer: dict = Depends(ca.current_customer)):
    history = [{**dict(r._mapping), "label": LEDGER_KINDS.get(r.kind, r.kind)}
               for r in await session.execute(text("""
        SELECT b.amount, b.kind, b.comment, b.created_at, o.number
          FROM bonus_ledger b LEFT JOIN orders o ON o.id = b.order_id
         WHERE b.customer_id = :c ORDER BY b.created_at DESC, b.id DESC LIMIT 100"""),
        {"c": customer["id"]})]
    personal = (await session.execute(text("SELECT personal_discount FROM customers WHERE id = :c"),
                                      {"c": customer["id"]})).scalar() or 0
    promos = [dict(r._mapping) for r in await session.execute(text("""
        SELECT pc.code, o.number, o.discount_amount, o.created_at
          FROM orders o JOIN promo_codes pc ON pc.id = o.promo_code_id
         WHERE o.customer_id = :c AND o.status <> 'cancelled' ORDER BY o.created_at DESC"""),
        {"c": customer["id"]})]
    waiting = (await session.execute(text("""
        SELECT coalesce(sum(floor((
                 (SELECT coalesce(sum(price * qty), 0) FROM order_items WHERE order_id = o.id)
                 - o.discount_amount - o.bonus_spent) * :p / 100)), 0)
          FROM orders o WHERE o.customer_id = :c AND o.status IN ('new', 'confirmed', 'paid', 'shipped')"""),
        {"c": customer["id"], "p": loyalty.ACCRUAL_PERCENT})).scalar()
    return page(request, "shop/account_bonus.html", customer, "bonus",
                await loyalty.balance(session, customer["id"]), history=history,
                personal=f"{Decimal(personal).normalize():f}%" if personal else None,
                promos=promos, waiting=int(waiting or 0),
                accrual=loyalty.ACCRUAL_PERCENT, limit=loyalty.SPEND_LIMIT_PERCENT)


# ------------------------------------------------------------------
# Мои адреса
# ------------------------------------------------------------------

MAX_ADDRESSES = 10


async def addresses_of(session: AsyncSession, customer_id: int) -> list[dict]:
    return [dict(r._mapping) for r in await session.execute(text("""
        SELECT id, title, country, city, cdek_code, postcode, street, house, block, flat, is_default
          FROM customer_addresses WHERE customer_id = :c
         ORDER BY is_default DESC, created_at"""), {"c": customer_id})]


@router.get("/account/addresses", response_class=HTMLResponse)
async def addresses_page(request: Request, session: AsyncSession = Depends(get_session),
                         customer: dict = Depends(ca.current_customer)):
    rows = await addresses_of(session, customer["id"])
    for r in rows:
        r["line"] = address.compose(r["country"], r["postcode"], r["city"], r["street"], r["house"],
                                    r["block"], r["flat"])[0]
    return page(request, "shop/account_addresses.html", customer, "addresses",
                await loyalty.balance(session, customer["id"]), addresses=rows, limit=MAX_ADDRESSES)


@router.get("/api/account/addresses")
async def addresses_api(session: AsyncSession = Depends(get_session),
                        customer: dict = Depends(ca.current_customer)):
    return await addresses_of(session, customer["id"])


class AddressIn(BaseModel):
    title: str | None = Field(default=None, max_length=40)
    country: str = Field(default="RU", max_length=2)
    city: str = Field(max_length=120)
    cdek_code: int | None = None
    postcode: str | None = Field(default=None, max_length=6)
    street: str = Field(max_length=120)
    house: str = Field(max_length=20)
    block: str | None = Field(default=None, max_length=20)
    flat: str | None = Field(default=None, max_length=20)
    is_default: bool = False


def checked_address(a: AddressIn) -> dict:
    city, err = addr_rules.check_city(a.city)
    require(err)
    require(addr_rules.check_address(a.country, a.postcode, a.street, a.house, a.block, a.flat))
    clean = lambda v: (v or "").strip() or None  # noqa: E731
    return {"title": clean(a.title), "country": a.country, "city": city, "cdek_code": a.cdek_code,
            "postcode": clean(a.postcode), "street": a.street.strip(), "house": a.house.strip(),
            "block": clean(a.block), "flat": clean(a.flat), "is_default": a.is_default}


async def save_address(session: AsyncSession, customer_id: int, data: dict,
                       address_id: int | None = None) -> int:
    """Новый адрес или правка. Первый адрес — основной; основной — один."""
    count = (await session.execute(text("SELECT count(*) FROM customer_addresses WHERE customer_id = :c"),
                                   {"c": customer_id})).scalar()
    if address_id is None and count >= MAX_ADDRESSES:
        raise HTTPException(409, f"Сохранить можно до {MAX_ADDRESSES} адресов — удалите ненужный")
    if not count:
        data["is_default"] = True
    if data["is_default"]:
        await session.execute(text("UPDATE customer_addresses SET is_default = false WHERE customer_id = :c"),
                              {"c": customer_id})
    params = {**data, "c": customer_id}
    if address_id is None:
        return (await session.execute(text("""
            INSERT INTO customer_addresses (customer_id, title, country, city, cdek_code, postcode,
                                            street, house, block, flat, is_default)
            VALUES (:c, :title, :country, :city, :cdek_code, :postcode, :street, :house, :block,
                    :flat, :is_default) RETURNING id"""), params)).scalar_one()
    n = (await session.execute(text("""
        UPDATE customer_addresses SET title = :title, country = :country, city = :city,
               cdek_code = :cdek_code, postcode = :postcode, street = :street, house = :house,
               block = :block, flat = :flat, is_default = :is_default OR is_default
         WHERE id = :id AND customer_id = :c"""), {**params, "id": address_id})).rowcount
    if not n:
        raise HTTPException(404, "Адрес не найден")
    return address_id


@router.post("/api/account/addresses", status_code=201)
async def address_add(payload: AddressIn, session: AsyncSession = Depends(get_session),
                      customer: dict = Depends(ca.current_customer)):
    aid = await save_address(session, customer["id"], checked_address(payload))
    await session.commit()
    return {"id": aid}


@router.put("/api/account/addresses/{address_id}")
async def address_edit(address_id: int, payload: AddressIn, session: AsyncSession = Depends(get_session),
                       customer: dict = Depends(ca.current_customer)):
    await save_address(session, customer["id"], checked_address(payload), address_id)
    await session.commit()
    return {"ok": True}


@router.delete("/api/account/addresses/{address_id}", status_code=204)
async def address_delete(address_id: int, session: AsyncSession = Depends(get_session),
                         customer: dict = Depends(ca.current_customer)):
    was_default = (await session.execute(text("""
        DELETE FROM customer_addresses WHERE id = :id AND customer_id = :c RETURNING is_default"""),
        {"id": address_id, "c": customer["id"]})).scalar()
    # Удалили основной — основным становится самый старый из оставшихся
    if was_default:
        await session.execute(text("""
            UPDATE customer_addresses SET is_default = true
             WHERE id = (SELECT id FROM customer_addresses WHERE customer_id = :c
                          ORDER BY created_at LIMIT 1)"""), {"c": customer["id"]})
    await session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------
# Профиль: данные, пароль, уведомления
# ------------------------------------------------------------------


async def profile_of(session: AsyncSession, customer_id: int) -> dict:
    r = (await session.execute(text("""
        SELECT id, name, phone, email, email_verified_at, password_hash IS NOT NULL AS has_password,
               notify_orders, notify_promo, created_at
          FROM customers WHERE id = :c"""), {"c": customer_id})).first()
    out = dict(r._mapping)
    out["identities"] = [dict(x._mapping) for x in await session.execute(text("""
        SELECT provider, display FROM customer_identities WHERE customer_id = :c ORDER BY created_at"""),
        {"c": customer_id})]
    return out


@router.get("/account/profile", response_class=HTMLResponse)
async def profile_page(request: Request, session: AsyncSession = Depends(get_session),
                       customer: dict = Depends(ca.current_customer)):
    return page(request, "shop/account_profile.html", customer, "profile",
                await loyalty.balance(session, customer["id"]),
                profile=await profile_of(session, customer["id"]))


class ProfileIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=200)


@router.patch("/api/account/profile")
async def profile_save(payload: ProfileIn, request: Request, session: AsyncSession = Depends(get_session),
                       customer: dict = Depends(ca.current_customer)):
    """Имя, телефон, email. Телефон и email — логины: чужой занять нельзя.
    Новый email не подтверждён — уходит письмо со ссылкой."""
    cur = await profile_of(session, customer["id"])
    sent = payload.model_fields_set
    upd: dict = {}
    if "name" in sent:
        require(people.check_name(payload.name, optional=False))
        upd["name"] = " ".join((payload.name or "").split())
    if "phone" in sent:
        phone = people.normalize_phone(payload.phone or "") if (payload.phone or "").strip() else None
        if (payload.phone or "").strip() and not phone:
            raise HTTPException(422, "Телефон в формате +7 900 000-00-00")
        if not phone and not cur["email"]:
            raise HTTPException(422, "Нужен телефон или email — по ним вы входите")
        if phone and phone != cur["phone"] and (await session.execute(text(
                "SELECT 1 FROM customers WHERE phone = :p AND id <> :c"), {"p": phone, "c": cur["id"]})).first():
            raise HTTPException(409, "Этот телефон уже у другого аккаунта")
        upd["phone"] = phone
    new_email = None
    if "email" in sent:
        email = people.normalize_email(payload.email or "") if (payload.email or "").strip() else None
        if (payload.email or "").strip() and not email:
            raise HTTPException(422, "Проверьте email: mail@example.ru")
        if not email and not upd.get("phone", cur["phone"]):
            raise HTTPException(422, "Нужен телефон или email — по ним вы входите")
        if email and email != (cur["email"] or "").lower():
            if (await session.execute(text(
                    "SELECT 1 FROM customers WHERE lower(email) = :e AND id <> :c"),
                    {"e": email, "c": cur["id"]})).first():
                raise HTTPException(409, "Этот email уже у другого аккаунта")
            new_email = email
        upd["email"] = email
    if not upd:
        return {"ok": True}
    sets = ", ".join(f"{k} = :{k}" for k in upd)
    if new_email:
        sets += ", email_verified_at = NULL, email_verify_sent_at = NULL"
    await session.execute(text(f"UPDATE customers SET {sets} WHERE id = :c"), {**upd, "c": cur["id"]})
    await session.commit()
    sent_mail = False
    if new_email and mailer.enabled():
        from .shop import send_verification
        sent_mail = await send_verification(session, cur["id"], new_email)
    return {"ok": True, "verify_sent": sent_mail}


class PasswordIn(BaseModel):
    current: str | None = Field(default=None, max_length=200)
    new: str = Field(min_length=6, max_length=200)


@router.post("/api/account/password")
async def password_change(payload: PasswordIn, session: AsyncSession = Depends(get_session),
                          customer: dict = Depends(ca.current_customer)):
    """Сменить пароль — с нынешним; вошли через соцсеть и пароля нет —
    задать новый, тогда можно входить и по телефону или email."""
    stored = (await session.execute(text("SELECT password_hash FROM customers WHERE id = :c"),
                                    {"c": customer["id"]})).scalar()
    if stored and not verify_password(payload.current or "", stored):
        raise HTTPException(422, "Нынешний пароль не подходит")
    require(people.check_new_password(payload.new))
    await session.execute(text("UPDATE customers SET password_hash = :h WHERE id = :c"),
                          {"h": hash_password(payload.new), "c": customer["id"]})
    await session.commit()
    return {"ok": True}


class NotifyIn(BaseModel):
    orders: bool | None = None
    promo: bool | None = None


@router.patch("/api/account/notifications")
async def notifications_save(payload: NotifyIn, session: AsyncSession = Depends(get_session),
                             customer: dict = Depends(ca.current_customer)):
    await session.execute(text("""
        UPDATE customers SET notify_orders = coalesce(CAST(:o AS boolean), notify_orders),
                             notify_promo = coalesce(CAST(:p AS boolean), notify_promo)
         WHERE id = :c"""), {"o": payload.orders, "p": payload.promo, "c": customer["id"]})
    await session.commit()
    return {"ok": True}
