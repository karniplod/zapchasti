"""Адрес доставки: подсказки улицы и дома, индекс по полному адресу.

Источник — DaData «Подсказки» (ключ DADATA_API_KEY). Ключ остаётся на
сервере: браузер спрашивает нас, мы — DaData. Без ключа подсказок нет,
адрес и индекс вводят руками — форма от этого не ломается.

Сам адрес собирается и проверяется здесь же: из отдельных полей —
страна, индекс, город, улица, дом, корпус, квартира — в одну строку,
как её пишут на посылке.
"""

import logging
import re
import time

import httpx

from .config import settings
from .validation.address import COUNTRIES, check_address

log = logging.getLogger("razbor.address")

DADATA_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"

_cache: dict = {}


def enabled() -> bool:
    return bool(settings.dadata_api_key)


async def suggest(level: str, q: str, city: str = "", street_fias: str = "",
                  country: str = "RU") -> list[dict]:
    """level — street или house. Улицы ищем в городе, дома — на улице:
    так подсказка не предлагает Ленинградскую из соседнего города."""
    q = (q or "").strip()
    if not enabled() or len(q) < 1 or level not in ("street", "house"):
        return []
    key = (level, q.lower(), city.lower(), street_fias, country)
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    if level == "street":
        loc = [{"city": city}, {"settlement": city}] if city else []
        body = {"query": q, "count": 8, "from_bound": {"value": "street"},
                "to_bound": {"value": "street"}, "restrict_value": True}
    else:
        loc = [{"street_fias_id": street_fias}] if street_fias else ([{"city": city}] if city else [])
        body = {"query": q, "count": 8, "from_bound": {"value": "house"},
                "to_bound": {"value": "house"}, "restrict_value": True}
    if loc:
        body["locations"] = loc
    if country != "RU":
        body["locations"] = [{**x, "country_iso_code": country} for x in (loc or [{}])]

    try:
        async with httpx.AsyncClient(timeout=6) as http:
            r = await http.post(DADATA_URL, json=body, headers={
                "Authorization": f"Token {settings.dadata_api_key}",
                "Content-Type": "application/json", "Accept": "application/json"})
            r.raise_for_status()
            rows = r.json().get("suggestions", [])
    except (httpx.HTTPError, ValueError) as e:
        log.warning("DaData: %s", e)
        return []

    out = []
    for s in rows:
        d = s.get("data") or {}
        out.append({
            "value": s.get("value"),
            "street": d.get("street_with_type") or d.get("street"),
            "street_fias": d.get("street_fias_id"),
            "house": " ".join(x for x in (d.get("house_type"), d.get("house")) if x)
                     if level == "house" else None,
            "house_num": d.get("house"),
            "block": " ".join(x for x in (d.get("block_type"), d.get("block")) if x) or None,
            "postcode": d.get("postal_code"),
            "city": d.get("city") or d.get("settlement"),
        })
    _cache[key] = (time.time() + 3600, out)
    return out


# ------------------------------------------------------------------
# Сборка адреса (правила полей — app/validation/address.py)
# ------------------------------------------------------------------

def compose(country: str, postcode: str | None, city: str, street: str, house: str,
            block: str | None, flat: str | None) -> tuple[str | None, str | None]:
    """→ (адрес одной строкой, текст ошибки). Индекс для России — шесть
    цифр; для Беларуси и Казахстана тоже шесть, но свой."""
    if err := check_address(country, postcode, street, house, block, flat):
        return None, err
    street, house = street.strip(), house.strip()
    block, flat = (block or "").strip(), (flat or "").strip()
    parts = [COUNTRIES[country], postcode, city.strip(), street,
             house if re.match(r"^(д|дом)\b", house, re.I) else f"д. {house}",
             block or None, f"кв. {flat}" if flat and not re.match(r"^(кв|оф)", flat, re.I) else flat or None]
    return ", ".join(p for p in parts if p), None
