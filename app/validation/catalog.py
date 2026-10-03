"""Детали и машины: состояние, тип детали, статусы, VIN, год, дата приёмки.

В браузере — Check.vinRule, year, pastDate в static/js/validation/rules.js.
"""

from datetime import date

from ..vin_decoder import normalize as normalize_vin

# Состояние детали — оценка разборщика
CONDITIONS = {"A", "B", "C", "D"}

# Тип детали: родная от автопроизводителя, ОЕМ-поставщик или аналог
ORIGINS = {
    "original": "Оригинал",
    "oem": "ОЕМ",
    "aftermarket": "Аналог",
}

PART_STATUSES = {"draft", "in_stock", "reserved", "sold", "written_off"}
DONOR_STATUSES = {"accepted", "dismantling", "dismantled", "scrapped"}


def check_condition(v: str | None) -> str | None:
    return None if v in CONDITIONS else "Состояние должно быть A, B, C или D"


def check_origin(v: str | None) -> str | None:
    return None if v in ORIGINS else "Тип детали: оригинал, ОЕМ или аналог"


def check_part_status(v: str | None) -> str | None:
    return None if v in PART_STATUSES else "Неизвестный статус"


def check_donor_status(v: str | None) -> str | None:
    return None if v in DONOR_STATUSES else "Неизвестный статус машины"


def check_vin(raw: str | None) -> tuple[str | None, str | None]:
    """→ (VIN заглавными без пробелов или None, ошибка). Пусто — не ошибка."""
    vin = normalize_vin(raw or "") or None
    if vin and len(vin) != 17:
        return None, "VIN должен быть из 17 символов"
    return vin, None


def check_year(year: int) -> str | None:
    """Год выпуска: с 1950 и не позже следующего — модельный год бывает вперёд."""
    top = date.today().year + 1
    return None if 1950 <= year <= top else f"Год: от 1950 до {top}"


def check_accepted_at(d: date) -> str | None:
    """Дата приёмки: задним числом вносят, вперёд — нет."""
    if d.year < 2000 or d > date.today():
        return "Дата приёмки: с 2000 года и не позже сегодня"
    return None


def check_weight(kg) -> str | None:
    return None if kg is None or kg >= 0 else "Вес не может быть отрицательным"
