"""Письма покупателю: статус заказа, посылки, баллы и скидки.

Что отправлять, решает покупатель в профиле (app/routers/cabinet.py):
- notify_orders — статус заказа и посылок (включено по умолчанию);
- notify_promo — баллы и промокоды: это реклама, только с согласия.

Письмо готовится в той же транзакции, что и правка заказа, а уходит
только после commit: откатилась правка — письма нет. Отправка — в
фоне, ответ на запрос её не ждёт. Куда: подтверждённый email покупателя,
а если его нет — email для чека из заказа.
"""

import asyncio
import html
import logging

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from . import mailer
from .config import settings

log = logging.getLogger("razbor.notify")

_KEY = "notify_outbox"


def _queue(session: AsyncSession, to: str, subject: str, body: str, link: str | None = None) -> None:
    session.sync_session.info.setdefault(_KEY, []).append((to, subject, body, link))


@event.listens_for(Session, "after_commit")
def _flush(sync_session) -> None:
    mails = sync_session.info.pop(_KEY, [])
    if not mails or not mailer.enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for to, subject, body, link in mails:
        loop.create_task(_send(to, subject, body, link))


@event.listens_for(Session, "after_rollback")
def _drop(sync_session) -> None:
    sync_session.info.pop(_KEY, None)


async def _send(to: str, subject: str, body: str, link: str | None) -> None:
    url = f"{settings.base_url}{link}" if link else settings.base_url
    text_body = f"{body}\n\n{url}\n\n— {settings.app_name}"
    html_body = ("<div style=\"font:15px/1.6 Arial,sans-serif;color:#333\">"
                 + "".join(f"<p>{html.escape(p)}</p>" for p in body.split("\n\n"))
                 + f"<p><a href=\"{html.escape(url)}\" style=\"color:#F54F0C\">Открыть в личном кабинете</a></p>"
                 f"<p style=\"color:#888;font-size:13px\">{html.escape(settings.app_name)} · "
                 f"уведомления настраиваются в профиле</p></div>")
    try:
        await mailer.send(to, subject, text_body, html_body)
    except Exception as e:  # письмо — не повод уронить что-то ещё
        log.warning("Уведомление для %s не ушло: %s", to, e)


async def _recipient(session: AsyncSession, customer_id: int | None, kind: str,
                     order_id: int | None = None) -> str | None:
    """Email покупателя, если он хочет такие письма."""
    if not customer_id:
        return None
    flag = "notify_orders" if kind == "orders" else "notify_promo"
    r = (await session.execute(text(f"""
        SELECT c.{flag} AS want, CASE WHEN c.email_verified_at IS NOT NULL THEN c.email END AS email,
               (SELECT o.contact_email FROM orders o WHERE o.id = CAST(:o AS bigint)) AS order_email
          FROM customers c WHERE c.id = :c"""), {"c": customer_id, "o": order_id})).first()
    if not r or not r.want:
        return None
    return r.email or r.order_email


STATUS_TEXT = {
    "confirmed": ("Заказ № {n} подтверждён",
                  "Мы проверили заказ № {n} — детали на месте, собираем его."),
    "paid": ("Оплата заказа № {n} получена",
             "Спасибо! Оплата заказа № {n} на {total} получена. Сообщим, когда заказ будет в пути."),
    "shipped": ("Заказ № {n} отправлен", "Заказ № {n} отправлен. Номера для отслеживания — в заказе."),
    "shipped_pickup": ("Заказ № {n} готов к выдаче",
                       "Заказ № {n} ждёт вас: {branch}. Возьмите с собой номер заказа."),
    "completed": ("Заказ № {n} выдан", "Заказ № {n} выдан. Спасибо, что выбрали нас!{bonus}"),
    "cancelled": ("Заказ № {n} отменён", "Заказ № {n} отменён.{refund} Если это ошибка — напишите нам."),
}


async def order_status(session: AsyncSession, order_id: int, status: str) -> None:
    o = (await session.execute(text("""
        SELECT o.id, o.number, o.customer_id, o.total, o.delivery_method, o.bonus_accrued, o.bonus_spent,
               (SELECT city || ', ' || name FROM branches WHERE id = o.pickup_branch_id) AS branch,
               (SELECT count(*) FROM order_shipments s WHERE s.order_id = o.id) AS ships
          FROM orders o WHERE o.id = :o"""), {"o": order_id})).first()
    if not o:
        return
    key = status
    if status == "shipped" and o.delivery_method == "pickup":
        key = "shipped_pickup"
    # Посылки отправляются по одной — о каждой отдельное письмо с номером
    # (shipment_sent); общее «отправлен» в этом случае лишнее
    if status == "shipped" and o.ships:
        return
    if key not in STATUS_TEXT:
        return
    to = await _recipient(session, o.customer_id, "orders", o.id)
    if not to:
        # Письма о заказах выключены, а о баллах — нет: про начисление скажем
        if status == "completed" and o.bonus_accrued:
            from .loyalty import balance
            await bonus(session, o.customer_id, o.bonus_accrued, f"за заказ № {o.number}",
                        await balance(session, o.customer_id))
        return
    subject, body = STATUS_TEXT[key]
    vals = {"n": o.number, "total": f"{o.total:,.0f} ₽".replace(",", " "), "branch": o.branch or "наш филиал",
            "bonus": f"\n\nЗа него начислено {o.bonus_accrued} баллов — ими можно оплатить следующий заказ."
                     if o.bonus_accrued else "",
            "refund": f" Потраченные на него {o.bonus_spent} баллов вернулись на счёт." if o.bonus_spent else ""}
    _queue(session, to, subject.format(**vals), body.format(**vals), f"/account/orders/{o.number}")


async def shipment_sent(session: AsyncSession, shipment_id: int) -> None:
    s = (await session.execute(text("""
        SELECT s.carrier, s.track_number, o.id AS order_id, o.number, o.customer_id, b.city,
               (SELECT count(*) FROM order_shipments x WHERE x.order_id = o.id) AS ships
          FROM order_shipments s JOIN orders o ON o.id = s.order_id
          LEFT JOIN branches b ON b.id = s.branch_id WHERE s.id = :s"""), {"s": shipment_id})).first()
    if not s:
        return
    to = await _recipient(session, s.customer_id, "orders", s.order_id)
    if not to:
        return
    from .delivery import CARRIERS, city_from, track_url
    what = f"Посылка {city_from(s.city)}" if s.ships > 1 else "Заказ"
    body = f"{what} по заказу № {s.number} отправлен{'а' if s.ships > 1 else ''} — {CARRIERS.get(s.carrier, 'службой доставки')}."
    if s.track_number:
        body += f"\n\nНомер для отслеживания: {s.track_number}"
        if url := track_url(s.carrier, s.track_number):
            body += f"\n\nГде посылка: {url}"
    _queue(session, to, f"{what} по заказу № {s.number} в пути", body, f"/account/orders/{s.number}")


async def bonus(session: AsyncSession, customer_id: int, amount: int, comment: str | None,
                balance: int) -> None:
    """Баллы от магазина — письмо, если покупатель согласился на такие."""
    to = await _recipient(session, customer_id, "promo")
    if not to or not amount:
        return
    word = "начислено" if amount > 0 else "списано"
    body = f"Вам {word} {abs(amount)} баллов{': ' + comment if comment else ''}.\n\nНа счету {balance} баллов — ими можно оплатить до 30% следующего заказа."
    _queue(session, to, f"Вам {word} {abs(amount)} баллов", body, "/account/bonus")


async def personal_discount(session: AsyncSession, customer_id: int, percent) -> None:
    to = await _recipient(session, customer_id, "promo")
    if not to or not percent:
        return
    p = f"{percent.normalize():f}" if hasattr(percent, "normalize") else str(percent)
    _queue(session, to, f"Ваша персональная скидка — {p}%",
           f"Для вас теперь действует персональная скидка {p}% на все детали. "
           "Она применится сама при оформлении заказа.", "/account/bonus")
