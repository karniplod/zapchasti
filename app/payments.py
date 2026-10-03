"""Онлайн-оплата заказа.

Провайдеров два, работают рядом — у покупателя на выбор:

  ЮKassa     карта и СБП одним договором, REST без SDK. Статус платежа
             никогда не берётся со слов браузера или из тела уведомления:
             каждый раз спрашиваем ЮKassa по id платежа.
  Робокасса  своя страница оплаты (карта, СБП, SberPay, Mir Pay). Об
             оплате сообщает запросом на ResultURL с подписью паролем №2 —
             только он отмечает платёж оплаченным; возврат человека на
             SuccessURL — лишь повод показать страницу заказа.

Оба подключаются ключами в .env. Робокасса видна покупателю и без
ключей (ROBOKASSA_PREVIEW=true, по умолчанию): заказ оформляется, а
вместо перехода в Робокассу — страница «скоро подключим, оплатите при
получении». Так способ виден заранее, а деньги не теряются.

Учебный режим (PAYMENT_DEMO=true) — для стенда: вместо банка своя
страница с кнопками «оплатить» и «отказаться». На рабочем сайте не
включать: оплаченным стал бы любой заказ.
"""

import hashlib
import logging
import uuid
from decimal import Decimal
from urllib.parse import urlencode

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import order_log
from .config import settings

log = logging.getLogger("razbor.payments")

YOOKASSA_API = "https://api.yookassa.ru/v3"
ROBOKASSA_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"


class PaymentError(Exception):
    """Банк не ответил или отказал — человеку показываем текст как есть."""


def yookassa_ready() -> bool:
    return bool(settings.yookassa_shop_id and settings.yookassa_secret_key)


def robokassa_ready() -> bool:
    return bool(settings.robokassa_login and settings.robokassa_password1
                and settings.robokassa_password2)


def methods() -> list[dict]:
    """Способы онлайн-оплаты для покупателя: code уходит в pay_with,
    provider — в payments.provider."""
    out = []
    if yookassa_ready() or settings.payment_demo:
        p = "yookassa" if yookassa_ready() else "demo"
        out += [
            {"code": "card", "title": "Банковской картой", "hint": "Visa, MasterCard, Мир",
             "provider": p},
            {"code": "sbp", "title": "СБП", "hint": "через приложение вашего банка",
             "provider": p},
        ]
    if robokassa_ready() or settings.robokassa_preview:
        out.append({"code": "robokassa", "title": "Робокасса",
                    "hint": "карта, СБП, SberPay, Mir Pay",
                    "provider": "robokassa"})
    return out


def method(code: str) -> dict | None:
    return next((m for m in methods() if m["code"] == code), None)


def title_of(code: str) -> str:
    """Подпись способа в истории платежей — и для тех, что уже выключены."""
    m = method(code)
    return m["title"] if m else {"card": "Банковской картой", "sbp": "СБП",
                                 "robokassa": "Робокасса"}.get(code, code)


# ------------------------------------------------------------------
# ЮKassa
# ------------------------------------------------------------------


def _auth() -> tuple[str, str]:
    return settings.yookassa_shop_id, settings.yookassa_secret_key


def receipt(order: dict) -> dict | None:
    """Чек по 54-ФЗ: позиция на каждую деталь и контакт, куда ЮKassa
    пришлёт чек. Цена — за штуку, количество — сколько купили. Сумма
    позиций обязана сойтись с суммой платежа до копейки, поэтому берём
    цены из order_items — те, что попали в заказ."""
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
            "description": (f"{i['name']} ({i['sku']})" if i.get("sku") else i["name"])[:128],
            "quantity": i.get("qty", 1),
            "amount": {"value": f"{Decimal(i['price']):.2f}", "currency": "RUB"},
            "vat_code": settings.yookassa_vat_code,
            "payment_mode": "full_payment",
            # Доставка в чеке — услуга, детали — товар
            "payment_subject": i.get("subject", "commodity"),
        } for i in order["items"]],
    }
    if settings.yookassa_tax_system_code:
        r["tax_system_code"] = settings.yookassa_tax_system_code
    return r


async def _yookassa_create(order: dict, code: str, return_url: str) -> tuple[str, str]:
    body = {
        "amount": {"value": f"{Decimal(order['total']):.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "payment_method_data": {"type": "bank_card" if code == "card" else "sbp"},
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
                headers={"Idempotence-Key": f"{order['number']}-{code}-{uuid.uuid4().hex}"})
        if r.status_code >= 400:
            log.warning("ЮKassa %s: %s", r.status_code, r.text[:300])
            raise PaymentError("Банк не принял платёж — попробуйте ещё раз или выберите оплату при получении")
        d = r.json()
        return d["id"], d["confirmation"]["confirmation_url"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("ЮKassa: %s", e)
        raise PaymentError("Банк не отвечает — попробуйте через минуту") from e


async def remote_status(external_id: str) -> str:
    """paid / failed / pending — по словам ЮKassa."""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.get(f"{YOOKASSA_API}/payments/{external_id}", auth=_auth())
            r.raise_for_status()
            st = r.json().get("status")
    except (httpx.HTTPError, ValueError) as e:
        log.warning("ЮKassa статус %s: %s", external_id, e)
        return "pending"
    return {"succeeded": "paid", "canceled": "failed"}.get(st, "pending")


# ------------------------------------------------------------------
# Робокасса
# ------------------------------------------------------------------


def _md5(*parts) -> str:
    return hashlib.md5(":".join(str(p) for p in parts).encode()).hexdigest()


def robokassa_sum(amount) -> str:
    return f"{Decimal(amount):.2f}"


def _robokassa_url(order: dict, inv_id: int) -> str:
    """Ссылка на страницу оплаты. InvId — наш номер платежа: по нему
    ResultURL найдёт, что оплачено. Подпись — паролем №1."""
    out_sum = robokassa_sum(order["total"])
    q = {
        "MerchantLogin": settings.robokassa_login,
        "OutSum": out_sum,
        "InvId": inv_id,
        "Description": f"Заказ № {order['number']}"[:100],
        "SignatureValue": _md5(settings.robokassa_login, out_sum, inv_id,
                               settings.robokassa_password1),
        "Culture": "ru",
    }
    if settings.robokassa_test:
        q["IsTest"] = 1
    return f"{ROBOKASSA_URL}?{urlencode(q)}"


def robokassa_result_ok(out_sum: str, inv_id: str, signature: str) -> bool:
    """Подпись уведомления ResultURL — паролем №2: его знают только
    Робокасса и мы, браузер покупателя её подделать не может."""
    want = _md5(out_sum, inv_id, settings.robokassa_password2)
    return robokassa_ready() and want.lower() == (signature or "").lower()


# ------------------------------------------------------------------
# Общее
# ------------------------------------------------------------------


async def create(order: dict, code: str, return_url: str, payment_id: int) -> tuple[str, str]:
    """Платёж у провайдера → (внешний id, ссылка, куда уйти платить).
    order: id, number, total, а для чека — items (name, sku, price) и
    email или phone покупателя. payment_id — наша строка в payments:
    Робокасса принимает только числовой номер счёта, им он и станет."""
    m = method(code)
    if not m:
        raise PaymentError("Этот способ оплаты сейчас недоступен")
    if m["provider"] == "demo":
        pid = "demo-" + uuid.uuid4().hex[:16]
        return pid, f"/pay/demo/{pid}"
    if m["provider"] == "robokassa":
        if not robokassa_ready():
            return "rk-preview", ""
        return str(payment_id), _robokassa_url(order, payment_id)
    return await _yookassa_create(order, code, return_url)


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
    if row:
        await order_log.log(session, row.order_id, "payment",
                            "Оплачен онлайн" if status == "paid" else "Онлайн-оплата не прошла")
    await session.commit()


async def refresh(session: AsyncSession, order_id: int) -> None:
    """Сверить с ЮKassa незакрытые платежи заказа — когда человек вернулся
    со страницы банка, уведомление могло ещё не прийти. Робокассу не
    спрашиваем: её ResultURL приходит раньше, чем человек возвращается."""
    if not yookassa_ready():
        return
    rows = (await session.execute(text("""
        SELECT id, external_id FROM payments
         WHERE order_id = :o AND status = 'pending' AND provider = 'yookassa'
           AND external_id IS NOT NULL"""), {"o": order_id})).all()
    for r in rows:
        await settle(session, r.id, await remote_status(r.external_id))
