"""Доставка службами: СДЭК, Яндекс Доставка, Почта России.

Что умеет: подсказать город, посчитать варианты доставки заказа из нашего
филиала до покупателя и показать пункты выдачи. Отправления в системах
служб не создаёт — это делает менеджер, пока нет договоров.

Цена всегда считается здесь, на сервере, — и для показа, и при оформлении
заказа заново. Цене из браузера не верим: её поправил бы кто угодно.

Каждая служба включается своими настройками (app/config.py). Не ответила
служба — её варианты просто не показываются, остальные работают.

Посылка: вес — сумма весов деталей (weight_kg, а если пусто — типовой вес
по размеру), габариты — самая крупная деталь заказа. Это грубее, чем
укладка в коробку, но служба всё равно перемерит при приёме.
"""

import asyncio
import logging
import time
from decimal import Decimal

import httpx

from .config import settings

log = logging.getLogger("razbor.delivery")

# Размер детали → типовая коробка, см, и вес, кг, если свой не указан
SIZES = {
    "S": {"label": "мелкая", "dims": (30, 20, 15), "kg": 1,
          "hint": "датчик, блок, реле, ручка"},
    "M": {"label": "средняя", "dims": (60, 40, 30), "kg": 5,
          "hint": "фара, генератор, стартер, зеркало"},
    "L": {"label": "крупная", "dims": (120, 60, 40), "kg": 15,
          "hint": "бампер, радиатор, крышка багажника"},
    "XL": {"label": "очень крупная", "dims": (160, 100, 60), "kg": 60,
           "hint": "дверь, капот, двигатель, КПП"},
}
DEFAULT_SIZE = "M"

CDEK_PROD = "https://api.cdek.ru/v2"
CDEK_EDU = "https://api.edu.cdek.ru/v2"
# Общие тестовые ключи из документации СДЭК — только для учебной среды
CDEK_EDU_KEYS = ("wqGwiQx0gg8mLtiEKsUinjVSICCjtTEP", "RmAmgvSgSl1yirlz9QupbzOJVqhCxcP5")

YANDEX_PROD = "https://b2b-authproxy.taxi.yandex.net"
YANDEX_TEST = "https://b2b.taxi.tst.yandex.net"
YANDEX_TEST_STATION = "fbed3aa1-2cc6-4370-ab4d-59c5cc9bb924"

CARRIERS = {"cdek": "СДЭК", "yandex": "Яндекс Доставка", "pochta": "Почта России"}
# Почта не принимает посылку тяжелее 20 кг — её тарификатор отвечает ошибкой
POCHTA_MAX_G = 20000
MODES = {"pvz": "до пункта выдачи", "door": "курьером до двери", "post": "до отделения"}


# ------------------------------------------------------------------
# Какие службы включены
# ------------------------------------------------------------------


def cdek_on() -> bool:
    return bool(settings.cdek_client_id and settings.cdek_client_secret) or settings.cdek_test


def yandex_on() -> bool:
    # Токен нужен всегда, и для тестовой среды тоже: тестовый токен из
    # документации Яндекса кладётся в .env, а не в код
    return bool(settings.yandex_delivery_token) and (
        bool(settings.yandex_delivery_station_id) or settings.yandex_delivery_test)


def pochta_on() -> bool:
    return settings.pochta_enabled


def enabled() -> list[str]:
    return [c for c, on in (("cdek", cdek_on()), ("yandex", yandex_on()),
                            ("pochta", pochta_on())) if on]


# ------------------------------------------------------------------
# Посылка
# ------------------------------------------------------------------


def package(items: list[dict]) -> dict:
    """items: size_class, weight_kg, qty. → вес в граммах и габариты, см."""
    weight = 0.0
    dims = (0, 0, 0)
    for i in items:
        size = SIZES.get(i.get("size_class") or DEFAULT_SIZE, SIZES[DEFAULT_SIZE])
        kg = float(i.get("weight_kg") or size["kg"])
        weight += kg * int(i.get("qty") or 1)
        if size["dims"] > dims:
            dims = size["dims"]
    return {"weight_g": max(100, int(weight * 1000)), "dims": dims}


# Простой кэш в памяти процесса: токены, коды городов, пункты выдачи —
# меняются редко, а запрос к службе — это секунда ожидания покупателя
_cache: dict = {}


def _cached(key, ttl: int):
    v = _cache.get(key)
    return v[1] if v and v[0] > time.time() else None


def _put(key, value, ttl: int):
    _cache[key] = (time.time() + ttl, value)
    return value


# ------------------------------------------------------------------
# СДЭК
# ------------------------------------------------------------------


def _cdek_base() -> str:
    return CDEK_EDU if settings.cdek_test else CDEK_PROD


async def _cdek_token(http: httpx.AsyncClient) -> str:
    tok = _cached("cdek_token", 0)
    if tok:
        return tok
    cid, secret = ((settings.cdek_client_id, settings.cdek_client_secret)
                   if settings.cdek_client_id and not settings.cdek_test else CDEK_EDU_KEYS)
    r = await http.post(f"{_cdek_base()}/oauth/token", data={
        "grant_type": "client_credentials", "client_id": cid, "client_secret": secret})
    r.raise_for_status()
    d = r.json()
    return _put("cdek_token", d["access_token"], int(d.get("expires_in", 3600)) - 60)


async def _cdek(http, method: str, path: str, **kw):
    r = await http.request(method, f"{_cdek_base()}{path}",
                           headers={"Authorization": f"Bearer {await _cdek_token(http)}"}, **kw)
    r.raise_for_status()
    return r.json()


async def cities(q: str) -> list[dict]:
    """Подсказка города — по справочнику СДЭК: у него код города, по
    которому считаются тарифы. Без СДЭК — пусто, город вводят как есть."""
    q = (q or "").strip()
    if len(q) < 2 or not cdek_on():
        return []
    key = ("cities", q.lower())
    hit = _cached(key, 0)
    if hit is not None:
        return hit
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            rows = await _cdek(http, "GET", "/location/suggest/cities",
                               params={"name": q, "country_code": "RU"})
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("СДЭК города: %s", e)
        return []
    out = [{"name": r["full_name"].split(",")[0], "full_name": r["full_name"],
            "cdek_code": r["code"]} for r in rows[:10]]
    return _put(key, out, 86400)


async def cdek_city_code(http, name: str) -> int | None:
    rows = await _cdek(http, "GET", "/location/suggest/cities",
                       params={"name": name, "country_code": "RU"})
    return rows[0]["code"] if rows else None


async def _cdek_quotes(http, origin: dict, dest: dict, pkg: dict) -> list[dict]:
    if not dest.get("cdek_code"):
        return []
    src = origin.get("cdek_city_code") or await cdek_city_code(http, origin["city"])
    if not src:
        return []
    origin["cdek_city_code"] = src
    L, W, H = pkg["dims"]
    d = await _cdek(http, "POST", "/calculator/tarifflist", json={
        "from_location": {"code": src}, "to_location": {"code": dest["cdek_code"]},
        "packages": [{"weight": pkg["weight_g"], "length": L, "width": W, "height": H}]})
    tariffs = {t["tariff_code"]: t for t in d.get("tariff_codes", [])}
    # Мы сдаём посылку в офис СДЭК («склад»). «Посылка» — до 30 кг,
    # тяжелее — «Магистральный экспресс», он и считается дешевле на весе
    heavy = pkg["weight_g"] > 30000
    pick = {"pvz": 62 if heavy else 136, "door": 122 if heavy else 137}
    out = []
    for mode, code in pick.items():
        t = tariffs.get(code)
        if not t:
            continue
        out.append({"carrier": "cdek", "mode": mode, "tariff": str(code),
                    "price": Decimal(str(t["delivery_sum"])),
                    "days": _days(t.get("period_min"), t.get("period_max"))})
    return out


async def _cdek_points(http, dest: dict) -> list[dict]:
    rows = await _cdek(http, "GET", "/deliverypoints",
                       params={"city_code": dest["cdek_code"], "type": "PVZ"})
    out = []
    for p in rows:
        loc = p["location"]
        # Коротко — «город, улица, дом»: полный адрес с индексом, страной
        # и областью в списке не читается. Индекс — в подписи под адресом
        short = ", ".join(x for x in (loc.get("city"), loc.get("address")) if x)
        out.append({"code": p["code"],
                     "name": " · ".join(x for x in (p["code"], loc.get("postal_code")) if x),
                     "address": short or loc.get("address_full") or p["code"],
                     "lat": loc.get("latitude"), "lon": loc.get("longitude"),
                     "hours": p.get("work_time")})
    return out


# ------------------------------------------------------------------
# Яндекс Доставка
# ------------------------------------------------------------------


def _ya():
    if settings.yandex_delivery_test:
        return (YANDEX_TEST, settings.yandex_delivery_token,
                settings.yandex_delivery_station_id or YANDEX_TEST_STATION)
    return YANDEX_PROD, settings.yandex_delivery_token, settings.yandex_delivery_station_id


async def _ya_post(http, path: str, body: dict) -> dict:
    base, tok, _ = _ya()
    r = await http.post(base + path, json=body,
                        headers={"Authorization": f"Bearer {tok}", "Accept-Language": "ru"})
    r.raise_for_status()
    return r.json()


async def _ya_geo(http, city: str) -> int | None:
    key = ("ya_geo", city.lower())
    hit = _cached(key, 0)
    if hit is not None:
        return hit
    d = await _ya_post(http, "/api/b2b/platform/location/detect", {"location": city})
    v = (d.get("variants") or [{}])[0].get("geo_id")
    return _put(key, v, 86400)


async def _ya_points(http, dest: dict) -> list[dict]:
    geo = await _ya_geo(http, dest["city"])
    if not geo:
        return []
    key = ("ya_points", geo)
    hit = _cached(key, 0)
    if hit is not None:
        return hit
    d = await _ya_post(http, "/api/b2b/platform/pickup-points/list",
                       {"geo_id": geo, "type": "pickup_point"})
    out = []
    for p in d.get("points", []):
        a = p.get("address") or {}
        addr = a.get("full_address") or ", ".join(x for x in (a.get("locality"), a.get("street"),
                                                              a.get("house")) if x)
        out.append({"code": p["id"], "name": p.get("name") or "Пункт выдачи", "address": addr,
                    "lat": (p.get("position") or {}).get("latitude"),
                    "lon": (p.get("position") or {}).get("longitude"),
                    "hours": None})
    return _put(key, out, 1800)


async def _ya_quote(http, pkg: dict, value_rub: Decimal, point: str) -> dict | None:
    _, _, station = _ya()
    L, W, H = pkg["dims"]
    d = await _ya_post(http, "/api/b2b/platform/pricing-calculator", {
        "source": {"platform_station_id": station},
        "destination": {"platform_station_id": point},
        "tariff": "self_pickup",
        "total_weight": pkg["weight_g"],
        "total_assessed_price": int(value_rub * 100),
        "client_price": 0, "payment_method": "already_paid",
        "places": [{"physical_dims": {"weight_gross": pkg["weight_g"], "dx": L, "dy": W, "dz": H}}]})
    price = Decimal(d["pricing_total"].split()[0])
    days = d.get("delivery_days")
    return {"carrier": "yandex", "mode": "pvz", "tariff": "self_pickup", "price": price,
            "days": _days(days, days), "point": point}


async def _ya_quotes(http, dest: dict, pkg: dict, value_rub: Decimal) -> list[dict]:
    """Цена у Яндекса зависит от пункта; для списка вариантов берём
    первый пункт города, при выборе пункта цена пересчитывается."""
    if dest.get("point"):
        try:
            return [await _ya_quote(http, pkg, value_rub, dest["point"])]
        except httpx.HTTPStatusError:
            return []             # в этот пункт Яндекс не возит
    # Не во все пункты есть доставка — пробуем несколько первых, пока
    # какой-нибудь не ответит ценой
    for p in (await _ya_points(http, dest))[:5]:
        try:
            q = await _ya_quote(http, pkg, value_rub, p["code"])
        except httpx.HTTPStatusError:
            continue
        q["point"] = None
        q["from"] = True          # «от …»: точная цена — после выбора пункта
        return [q]
    return []


# ------------------------------------------------------------------
# Почта России
# ------------------------------------------------------------------


async def _pochta_quotes(http, origin: dict, dest: dict, pkg: dict) -> list[dict]:
    """Посылка онлайн нестандартная (4030) — подходит и для длинных
    деталей; индекс получателя обязателен."""
    if not dest.get("postcode") or not origin.get("postcode") or pkg["weight_g"] > POCHTA_MAX_G:
        return []
    r = await http.get("https://tariff.pochta.ru/v2/calculate/tariff/delivery", params={
        "json": "", "object": 4030, "from": origin["postcode"], "to": dest["postcode"],
        "weight": pkg["weight_g"], "pack": 10})
    r.raise_for_status()
    d = r.json()
    if d.get("errors") or not d.get("paynds"):
        return []
    dl = d.get("delivery") or {}
    return [{"carrier": "pochta", "mode": "post", "tariff": "4030",
             "price": (Decimal(d["paynds"]) / 100).quantize(Decimal("1")),
             "days": _days(dl.get("min"), dl.get("max"))}]


# ------------------------------------------------------------------
# Общее
# ------------------------------------------------------------------


def _days(a, b) -> str:
    a, b = (a or 0), (b or 0)
    if not a and not b:
        return ""
    a = max(a, 1)
    b = max(b, a)
    return f"{a} дн." if a == b else f"{a}–{b} дн."


def pochta_issue(items: list[dict], postcode: str | None, offered: bool) -> str | None:
    """Почему Почты нет среди вариантов — чтобы показать плитку с причиной,
    а не прятать вариант молча. None — либо Почта посчитала, либо ей
    просто нужен индекс (это фронт показывает сам)."""
    if not pochta_on() or offered:
        return None
    if package(items)["weight_g"] > POCHTA_MAX_G:
        return "Почта принимает посылки до 20 кг — этот заказ тяжелее"
    if postcode:
        return "Почта не посчитала доставку по этому индексу — проверьте его"
    return None


def title(o: dict) -> str:
    return f"{CARRIERS.get(o['carrier'], o['carrier'])} — {MODES.get(o['mode'], o['mode'])}"


async def quotes(origin: dict, dest: dict, items: list[dict], value_rub: Decimal) -> list[dict]:
    """Все варианты доставки по включённым службам, дешёвые сверху.
    origin: city, postcode, cdek_city_code филиала отправки.
    dest: city, cdek_code, postcode, point (пункт Яндекса, если выбран)."""
    pkg = package(items)
    out: list[dict] = []

    async def one(name, call):
        try:
            return await call()
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as e:
            log.warning("%s: расчёт не удался: %s", name, e)
            return []

    # Службы спрашиваем разом, а не по очереди: покупатель ждёт самую
    # медленную, а не сумму всех
    async with httpx.AsyncClient(timeout=10) as http:
        calls = {
            "cdek": lambda: _cdek_quotes(http, origin, dest, pkg),
            "yandex": lambda: _ya_quotes(http, dest, pkg, value_rub),
            "pochta": lambda: _pochta_quotes(http, origin, dest, pkg),
        }
        on = [n for n in calls if n in enabled()]
        for got in await asyncio.gather(*(one(n, calls[n]) for n in on)):
            out += got
    for o in out:
        o["title"] = title(o)
    return sorted(out, key=lambda o: o["price"])


async def points(carrier: str, dest: dict) -> list[dict]:
    if carrier not in enabled():
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            if carrier == "cdek" and dest.get("cdek_code"):
                key = ("cdek_points", dest["cdek_code"])
                hit = _cached(key, 0)
                return hit if hit is not None else _put(key, await _cdek_points(http, dest), 1800)
            if carrier == "yandex" and dest.get("city"):
                return await _ya_points(http, dest)
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("%s: пункты выдачи: %s", carrier, e)
    return []
