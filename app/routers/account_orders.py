"""Правка заказа покупателем в личном кабинете — пока заказ «новый».

Пока менеджер не взял заказ в работу, покупатель сам поправит то, в чём
ошибся: получателя и телефон, адрес (служба и способ доставки — те же),
филиал самовывоза, количество деталей и комментарий. Может и отменить
заказ. Подтверждён, оплачен или отправлен — правит только менеджер.

Цену доставки считает сервер — по новому адресу и составу, той же
службой. Перед сохранением покупатель видит «было → станет»: тот же
код правки выполняется и откатывается (/preview), так что показанное
и сохранённое не расходятся.

Изменилась сумма — неоплаченные ссылки на оплату аннулируются: по ним
заплатили бы старую сумму (см. payments.settle).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import customer_auth as ca
from .. import order_log
from ..database import get_session
from .orders_admin import (apply_quote, money, quote_order, return_stock, set_order_status,
                           shipping_address, sync_totals, take_stock)

router = APIRouter(tags=["account-orders"])


def customer_can_edit(o) -> bool:
    """Покупатель правит заказ, пока он новый и не оплачен."""
    return o["status"] == "new" and not o["paid_at"]


class ItemQty(BaseModel):
    id: int
    qty: int = Field(ge=0, le=999)            # 0 — убрать из заказа


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
        if all(want.get(i, r.qty) == 0 for i, r in rows.items()):
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
    if o2.delivery_carrier and (payload.items or sent & ADDRESS):
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
            "days": o2.delivery_days, "changes": changes}


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

