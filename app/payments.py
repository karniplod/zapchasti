"""Онлайн-оплата заказа.

Провайдер один — ЮKassa: карты и СБП одним договором, REST без SDK.
Подключается ключами в .env; без них онлайн-оплаты нет, и заказ
оплачивается при получении, как раньше.

Учебный режим (PAYMENT_DEMO=true) — для стенда: вместо банка своя
страница с кнопками «оплатить» и «отказаться». Деньги не двигаются,
но весь путь заказа — от корзины до «оплачен» — проходится руками.
Включать его на рабочем сайте нельзя: оплаченным стал бы любой заказ.

Статус платежа никогда не берётся со слов браузера или из тела
уведомления: каждый раз спрашиваем банк по id платежа. Иначе
«оплачено» подделывалось бы одним запросом.
"""

import logging
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings

log = logging.getLogger("razbor.payments")

YOOKASSA_API = "https://api.yookassa.ru/v3"

METHODS = [
    {"code": "card", "title": "Банковской картой"},
    {"code": "sbp", "title": "СБП — через приложение банка"},
]


class PaymentError(Exception):
    """Банк не ответил или отказал — человеку показываем текст как есть."""


def provider() -> str | None:
    if settings.yookassa_shop_id and settings.yookassa_secret_key:
        return "yookassa"
    if settings.payment_demo:
        return "demo"
    return None


def methods() -> list[dict]:
    return METHODS if provider() else []


def _auth() -> tuple[str, str]:
    return settings.yookassa_shop_id, settings.yookassa_secret_key


def receipt(order: dict) -> dict | None:
    """Чек по 54-ФЗ: позиция на каждую деталь и контакт, куда ЮKassa
    пришлёт чек. Деталь штучная — количество всегда 1. Сумма позиций
    обязана сойтись с суммой платежа до копейки, поэтому берём цены из
    order_items — те, что попали в заказ."""
    if not settings.yookassa_receipts:
        return None
    contact = {}
    if order.get("email"):
        contact["email"] = order["email"]
    elif order.get("phone"):
        contact["phone"] = order["phone"].lstrip("+")    # ЮKassa: 79001234567
    r = {
        "customer": contact,
        "items": [{
            "description": f"{i['name']} ({i['sku']})"[:128],
            "quantity": 1,
            "amount": {"value": f"{Decimal(i['price']):.2f}", "currency": "RUB"},
            "vat_code": settings.yookassa_vat_code,
            "payment_mode": "full_payment",
            "payment_subject": "commodity",
        } for i in order["items"]],
    }
    if settings.yookassa_tax_system_code:
        r["tax_system_code"] = settings.yookassa_tax_system_code
    return r


async def create(order: dict, method: str, return_url: str) -> tuple[str, str]:
    """Платёж у провайдера → (id платежа, ссылка на страницу оплаты).
    order: id, number, total, а для чека — items (name, sku, price) и
    email или phone покупателя."""
    if provider() == "demo":
        pid = "demo-" + uuid.uuid4().hex[:16]
        return pid, f"/pay/demo/{pid}"

    body = {
        "amount": {"value": f"{Decimal(order['total']):.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "payment_method_data": {"type": "bank_card" if method == "card" else "sbp"},
        "description": f"Заказ № {order['number']}"[:128],
        "metadata": {"order_number": order["number"]},
    }
    rc = receipt(order)
    if rc:
        body["receipt"] = rc
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(
                f"{YOOKASSA_API}/payments", json=body, auth=_auth(),
                # Ключ идемпотентности: оборвался ответ — повтор того же
                # запроса не создаст второй платёж
                headers={"Idempotence-Key": f"{order['number']}-{method}-{uuid.uuid4().hex}"})
        if r.status_code >= 400:
            log.warning("ЮKassa %s: %s", r.status_code, r.text[:300])
            raise PaymentError("Банк не принял платёж — попробуйте ещё раз или выберите оплату при получении")
        d = r.json()
        return d["id"], d["confirmation"]["confirmation_url"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("ЮKassa: %s", e)
        raise PaymentError("Банк не отвечает — попробуйте через минуту") from e


async def remote_status(external_id: str) -> str:
    """paid / failed / pending — по словам банка."""
    if external_id.startswith("demo-"):
        return "pending"   # учебный платёж меняет только страница /pay/demo
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(f"{YOOKASSA_API}/payments/{external_id}", auth=_auth())
            r.raise_for_status()
            st = r.json().get("status")
    except (httpx.HTTPError, ValueError) as e:
        log.warning("ЮKassa статус %s: %s", external_id, e)
        return "pending"
    return {"succeeded": "paid", "canceled": "failed"}.get(st, "pending")


async def settle(session: AsyncSession, payment_id: int, status: str) -> None:
    """Записать исход платежа. Оплачен — заказ тоже оплачен.
    Повторный вызов ничего не портит: статус меняется только с pending."""
    if status not in ("paid", "failed"):
        return
    row = (await session.execute(text("""
        UPDATE payments SET status = :st,
               paid_at = CASE WHEN :st = 'paid' THEN now() END
         WHERE id = :id AND status = 'pending'
        RETURNING order_id"""), {"st": status, "id": payment_id})).first()
    if row and status == "paid":
        await session.execute(text("""
            UPDATE orders SET status = 'paid', paid_at = now()
             WHERE id = :o AND status IN ('new', 'confirmed')"""), {"o": row.order_id})
    await session.commit()


async def refresh(session: AsyncSession, order_id: int) -> None:
    """Сверить с банком все незакрытые платежи заказа — когда человек
    вернулся со страницы банка, уведомление могло ещё не прийти."""
    rows = (await session.execute(text("""
        SELECT id, external_id FROM payments
         WHERE order_id = :o AND status = 'pending' AND provider = 'yookassa'
           AND external_id IS NOT NULL"""), {"o": order_id})).all()
    for r in rows:
        await settle(session, r.id, await remote_status(r.external_id))
