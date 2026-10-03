"""Бонусные баллы, промокоды и персональные скидки.

Правила (решение владельца, 3 октября 2026):
- 1 балл = 1 ₽. Начисляется 5% от того, что за товары заплачено деньгами
  (после скидки и баллов), когда заказ выдан. За доставку не начисляется.
- Баллами можно оплатить до 30% товаров после скидки; доставку — нет.
- Скидки не суммируются: из персональной скидки покупателя и промокода
  работает бо́льшая.
- Отмена заказа: списанные баллы возвращаются, начисленные — снимаются.
- Баллы не сгорают.

Баланс — сумма строк bonus_ledger: каждое движение видно в кабинете.
Промокоды и персональные скидки ведёт менеджер (страница «Промокоды»,
карточка заказа). Без commit — его делает вызывающий.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ACCRUAL_PERCENT = 5
SPEND_LIMIT_PERCENT = 30


def rub(v) -> Decimal:
    return Decimal(v).quantize(Decimal("1"), ROUND_HALF_UP)


async def balance(session: AsyncSession, customer_id: int) -> int:
    return int((await session.execute(text(
        "SELECT coalesce(sum(amount), 0) FROM bonus_ledger WHERE customer_id = :c"),
        {"c": customer_id})).scalar())


async def move(session: AsyncSession, customer_id: int, amount: int, kind: str,
               comment: str | None = None, order_id: int | None = None,
               user: dict | None = None) -> None:
    """Движение баллов: + начислено, − списано."""
    if not amount:
        return
    await session.execute(text("""
        INSERT INTO bonus_ledger (customer_id, order_id, amount, kind, comment, user_id)
        VALUES (:c, :o, :a, :k, :m, :u)"""),
        {"c": customer_id, "o": order_id, "a": int(amount), "k": kind, "m": comment,
         "u": user["id"] if user else None})


def discount_of(goods: Decimal, kind: str | None, value) -> Decimal:
    """Скидка в рублях: процент от товаров или сумма — не больше товаров."""
    if not kind or not value:
        return Decimal(0)
    d = goods * Decimal(value) / 100 if kind == "percent" else Decimal(value)
    return min(rub(d), goods)


def bonus_cap(goods_after_discount: Decimal) -> int:
    """Сколько баллов можно списать: до 30% товаров после скидки."""
    return int(goods_after_discount * SPEND_LIMIT_PERCENT / 100)


async def check_promo(session: AsyncSession, code: str, customer_id: int | None,
                      goods: Decimal):
    """Промокод → (строка промокода, ошибка)."""
    code = (code or "").strip().upper()
    if not code:
        return None, None
    p = (await session.execute(text("SELECT * FROM promo_codes WHERE code = :c"),
                               {"c": code})).first()
    today = date.today()
    if not p or not p.active:
        return None, "Такого промокода нет"
    if p.starts_at and today < p.starts_at:
        return None, f"Промокод действует с {p.starts_at:%d.%m.%Y}"
    if p.ends_at and today > p.ends_at:
        return None, "Срок действия промокода закончился"
    if goods < p.min_total:
        return None, f"Промокод — для заказа от {rub(p.min_total):,} ₽".replace(",", " ")
    used = (await session.execute(text("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE customer_id = CAST(:c AS bigint)) AS mine
          FROM orders WHERE promo_code_id = :p AND status <> 'cancelled'"""),
        {"p": p.id, "c": customer_id})).first()
    if p.max_uses is not None and used.total >= p.max_uses:
        return None, "Промокод уже израсходован"
    if p.once_per_customer and used.mine:
        return None, "Вы уже использовали этот промокод"
    return p, None


async def best_discount(session: AsyncSession, customer: dict | None, goods: Decimal,
                        promo_code: str | None = None) -> dict:
    """Скидка заказа: из персональной и промокода — бо́льшая.
    → {source, kind, value, amount, promo_id, promo_error, personal}."""
    personal = Decimal(0)
    if customer:
        personal = Decimal((await session.execute(text(
            "SELECT personal_discount FROM customers WHERE id = :c"),
            {"c": customer["id"]})).scalar() or 0)
    # promo_error — промокод не годится; promo_note — годится, но
    # персональная скидка больше, и работает она
    out = {"source": None, "kind": None, "value": None, "amount": Decimal(0),
           "promo_id": None, "promo_error": None, "promo_note": None, "personal": personal}
    if personal:
        out.update(source="personal", kind="percent", value=personal,
                   amount=discount_of(goods, "percent", personal))
    if promo_code:
        p, err = await check_promo(session, promo_code, customer and customer["id"], goods)
        out["promo_error"] = err
        if p:
            amount = discount_of(goods, p.kind, p.value)
            if amount > out["amount"]:
                out.update(source="promo", kind=p.kind, value=p.value, amount=amount,
                           promo_id=p.id)
            else:
                out["promo_note"] = "Ваша персональная скидка больше — работает она"
    return out


async def on_status(session: AsyncSession, order_id: int, status: str) -> None:
    """Баллы следуют за статусом заказа: выдан — начислить, отменён —
    вернуть списанные и снять начисленные."""
    o = (await session.execute(text("""
        SELECT id, number, customer_id, bonus_spent, bonus_accrued, discount_amount,
               (SELECT coalesce(sum(price * qty), 0) FROM order_items WHERE order_id = orders.id) AS goods
          FROM orders WHERE id = :o"""), {"o": order_id})).first()
    if not o or not o.customer_id:
        return
    if status == "completed" and not o.bonus_accrued:
        paid = Decimal(o.goods) - Decimal(o.discount_amount) - o.bonus_spent
        amount = int(paid * ACCRUAL_PERCENT / 100)
        if amount > 0:
            await move(session, o.customer_id, amount, "accrual",
                       f"{ACCRUAL_PERCENT}% за заказ № {o.number}", o.id)
            await session.execute(text("UPDATE orders SET bonus_accrued = :a WHERE id = :o"),
                                  {"a": amount, "o": o.id})
    elif status == "cancelled":
        if o.bonus_spent:
            await move(session, o.customer_id, o.bonus_spent, "refund",
                       f"Возврат: заказ № {o.number} отменён", o.id)
            await session.execute(text("UPDATE orders SET bonus_spent = 0 WHERE id = :o"),
                                  {"o": o.id})
        if o.bonus_accrued:
            await move(session, o.customer_id, -o.bonus_accrued, "revoke",
                       f"Заказ № {o.number} отменён", o.id)
            await session.execute(text("UPDATE orders SET bonus_accrued = 0 WHERE id = :o"),
                                  {"o": o.id})


def spread(lines: list[dict], reduction: Decimal) -> list[dict]:
    """Скидку и баллы — в цены товаров: в кассовом чеке сумма позиций
    обязана совпасть с оплатой до копейки. Делим пропорционально сумме
    строки; остаток копеек — последней строке, а строку в несколько штук,
    где цена за штуку не делится ровно, разбиваем на две."""
    goods = [i for i in lines if i.get("subject", "commodity") == "commodity"]
    rest = [i for i in lines if i not in goods]
    total = sum(Decimal(i["price"]) * i["qty"] for i in goods)
    if not reduction or not total:
        return lines
    reduction = min(Decimal(reduction), total)
    out, left = [], reduction
    for n, i in enumerate(goods):
        line = Decimal(i["price"]) * i["qty"]
        cut = left if n == len(goods) - 1 else (line * reduction / total).quantize(Decimal("0.01"))
        left -= cut
        new_sum = line - cut
        unit = (new_sum / i["qty"]).quantize(Decimal("0.01"))
        if unit * i["qty"] == new_sum or i["qty"] == 1:
            out.append({**i, "price": unit if i["qty"] > 1 else new_sum})
        else:
            # 3 шт за 100 ₽: 2 шт по 33,33 и 1 шт по 33,34
            out.append({**i, "price": unit, "qty": i["qty"] - 1})
            out.append({**i, "price": new_sum - unit * (i["qty"] - 1), "qty": 1})
    return out + rest
