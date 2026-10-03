"""Адрес доставки по полям: страна, индекс, улица, дом, корпус, квартира.

Сборка адреса одной строкой и подсказки DaData — в app/address.py; здесь
только правила полей. В браузере — Check.street, house, addrPart,
postcode в static/js/validation/rules.js.
"""

import re

COUNTRIES = {"RU": "Россия", "BY": "Беларусь", "KZ": "Казахстан"}

STREET_RE = re.compile(r"^[\w .,'«»\"()№/-]{2,120}$")
HOUSE_RE = re.compile(r"^[\w/ .-]{1,20}$")
FLAT_RE = re.compile(r"^[\w/ .-]{1,20}$")         # корпус, строение, квартира, офис
POSTCODE_RE = re.compile(r"^\d{6}$")              # в России, Беларуси и Казахстане


def check_postcode(raw: str | None, required: bool = False) -> tuple[str | None, str | None]:
    """→ (индекс или None, ошибка)."""
    v = (raw or "").strip() or None
    if not v:
        return None, ("Для Почты России нужен индекс" if required else None)
    return (v, None) if POSTCODE_RE.match(v) else (None, "Индекс — шесть цифр")


def check_address(country: str, postcode: str | None, street: str, house: str,
                  block: str | None, flat: str | None) -> str | None:
    """Поля адреса для доставки до двери или до отделения."""
    street, house = (street or "").strip(), (house or "").strip()
    block, flat = (block or "").strip(), (flat or "").strip()
    if country not in COUNTRIES:
        return "Выберите страну"
    if not STREET_RE.match(street):
        return "Улица: от 2 символов — буквы, цифры, точка, дефис"
    if not HOUSE_RE.match(house) or not re.search(r"\d", house):
        return "Дом: номер, например 10 или 10/2"
    if block and not FLAT_RE.match(block):
        return "Корпус или строение: до 20 символов"
    if flat and not FLAT_RE.match(flat):
        return "Квартира или офис: до 20 символов"
    return check_postcode(postcode)[1]


def check_city(raw: str | None) -> tuple[str | None, str | None]:
    city = " ".join((raw or "").split())
    return (city, None) if len(city) >= 2 else (None, "Укажите город доставки")
