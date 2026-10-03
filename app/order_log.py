"""Лента заказа: что происходило с заказом и кто это сделал.

Пишут все, кто меняет заказ: оформление на сайте, онлайн-оплата,
менеджер в карточке, филиал в посылке, приложение сотрудника. Лента —
ответ на вопрос «почему адрес другой» и «кто отменил», как история
сделки в CRM. Без commit: запись идёт в одной транзакции с правкой.
"""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def log(session: AsyncSession, order_id: int, kind: str, message: str,
              user: dict | None = None, data: dict | None = None) -> None:
    """kind: created, status, edit, item, shipment, delivery, payment, note.
    user — сотрудник; None — покупатель или сам сайт."""
    await session.execute(text("""
        INSERT INTO order_events (order_id, user_id, kind, text, data)
        VALUES (:o, :u, :k, :t, CAST(:d AS jsonb))"""),
        {"o": order_id, "u": user["id"] if user else None, "k": kind, "t": message,
         "d": json.dumps(data, ensure_ascii=False, default=str) if data else None})
