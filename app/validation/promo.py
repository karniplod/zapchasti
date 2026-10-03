"""Промокоды и персональные скидки — поля, которые заводит менеджер.

В браузере те же правила — static/js/admin/promo.js.
"""

import re
from datetime import date
from decimal import Decimal

CODE_RE = re.compile(r"^[A-Z0-9_-]{3,30}$")
KINDS = {"percent": "%", "amount": "₽"}


def check_code(raw: str | None) -> tuple[str | None, str | None]:
    """→ (код заглавными, ошибка). Латиница и цифры: код диктуют по телефону."""
    code = (raw or "").strip().upper()
    if not CODE_RE.match(code):
        return None, "Код: латиница, цифры, дефис — от 3 до 30 знаков"
    return code, None


def check_value(kind: str, value: Decimal | None) -> str | None:
    if kind not in KINDS:
        return "Скидка: в процентах или в рублях"
    if value is None or value <= 0:
        return "Размер скидки — больше нуля"
    if kind == "percent" and value > 90:
        return "Скидка в процентах — не больше 90%"
    return None


def check_dates(starts: date | None, ends: date | None) -> str | None:
    if starts and ends and ends < starts:
        return "Окончание раньше начала"
    return None


def check_personal(value: Decimal) -> str | None:
    return None if 0 <= value <= 50 else "Персональная скидка — от 0 до 50%"
