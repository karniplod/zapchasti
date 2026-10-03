"""Заказы: номер отслеживания посылки.

Получатель — people.recipient, адрес — address.check_address.
"""

import re

# СДЭК — цифры, Почта — RA123456789RU, Яндекс — буквы, цифры и дефисы
TRACK_RE = re.compile(r"^[A-Za-z0-9-]{4,40}$")


def check_track(raw: str | None) -> tuple[str | None, str | None]:
    """→ (номер без пробелов или пустая строка — «стереть», ошибка)."""
    v = (raw or "").strip().replace(" ", "")
    if v and not TRACK_RE.match(v):
        return None, "Номер отслеживания: латиница, цифры и дефис, 4–40 знаков"
    return v, None
