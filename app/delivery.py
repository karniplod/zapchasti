"""Доставка службами: СДЭК, Яндекс Доставка, Почта России.

Что умеет: подсказать город, посчитать варианты доставки заказа из нашего
филиала до покупателя и показать пункты выдачи. Отправления в системах
служб не создаёт — это делает менеджер, пока нет договоров.

Цена всегда считается здесь, на сервере, — и для показа, и при оформлении
заказа заново. Цене из браузера не верим: её поправил бы кто угодно.

Каждая служба включается своими настройками (app/config.py). Не ответила
служба — её варианты просто не показываются, остальные работают.

Посылка — детали заказа из одного филиала: из каждого города заказ едет
своей посылкой. Вес — сумма весов деталей (weight_kg, а если пусто —
типовой вес по размеру), габариты — самая крупная деталь посылки. Это
грубее, чем укладка в коробку, но служба всё равно перемерит при приёме.
"""

import asyncio
import json
import logging
import time
from decimal import Decimal

import httpx
from sqlalchemy import text

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
    # документации Яндекса кладётся в .env, а не в код. Склад отгрузки —
    # у каждого филиала свой (branches.yandex_station_id)
    return bool(settings.yandex_delivery_token)


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
    # Мы сдаём посылку в офис СДЭК («склад»). «Посылка» — до 30 кг,
    # тяжелее — «Магистральный экспресс», он и считается дешевле на весе
    heavy = pkg["weight_g"] > 30000
    pick = {"pvz": 62 if heavy else 136, "door": 122 if heavy else 137}
    try:
        d = await _cdek(http, "POST", "/calculator/tarifflist", json={
            "from_location": {"code": src}, "to_location": {"code": dest["cdek_code"]},
            "packages": [{"weight": pkg["weight_g"], "length": L, "width": W, "height": H}]})
    except httpx.HTTPStatusError as e:
        # Учебная среда СДЭК бывает сломана: её калькулятор не узнаёт ни
        # одного города, хотя подсказка городов и пункты выдачи работают.
        # Чтобы оформление можно было пройти целиком, в учебном режиме
        # считаем примерную цену сами и честно её так подписываем.
        # С боевыми ключами этого нет — там ошибка есть ошибка
        if settings.cdek_test and e.response.status_code == 400:
            log.warning("СДЭК учебный: калькулятор не ответил, цена примерная")
            return _cdek_estimate(origin, dest, pkg, pick)
        raise
    tariffs = {t["tariff_code"]: t for t in d.get("tariff_codes", [])}
    out = []
    for mode, code in pick.items():
        t = tariffs.get(code)
        if not t:
            continue
        out.append({"carrier": "cdek", "mode": mode, "tariff": str(code),
                    "price": Decimal(str(t["delivery_sum"])),
                    **_span(t.get("period_min"), t.get("period_max"))})
    return out


# Дальние города: посылка идёт неделю и больше и стоит дороже
_FAR = {"Владивосток", "Хабаровск", "Южно-Сахалинск", "Петропавловск-Камчатский",
        "Магадан", "Якутск", "Благовещенск", "Норильск", "Анадырь"}


def _cdek_estimate(origin: dict, dest: dict, pkg: dict, pick: dict) -> list[dict]:
    """Примерный тариф СДЭК — только для учебного режима, когда учебный
    калькулятор не работает. Порядок цен близок к настоящим «Посылкам»:
    база плюс килограммы, до двери дороже, дальний Восток — дороже и дольше."""
    kg = pkg["weight_g"] / 1000
    same = origin.get("cdek_city_code") == dest.get("cdek_code")
    far = (origin.get("city") in _FAR) != (dest.get("city") in _FAR)
    base = 220 if same else 390 + (1400 if far else 0)
    per_kg = 18 if same else (95 if far else 42)
    days = (1, 2) if same else ((8, 13) if far else (2, 5))
    out = []
    for mode, code in pick.items():
        price = base + per_kg * kg + (210 if mode == "door" else 0)
        out.append({"carrier": "cdek", "mode": mode, "tariff": str(code),
                    "price": Decimal(round(price / 5) * 5), "estimate": True, **_span(*days)})
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


def _ya_station(origin: dict) -> str | None:
    """Склад, с которого Яндекс забирает посылки филиала. В тестовой
    среде у филиалов складов нет — все везут с тестового (он в Москве)."""
    if origin.get("yandex_station_id"):
        return origin["yandex_station_id"]
    if settings.yandex_delivery_test:
        return settings.yandex_delivery_station_id or YANDEX_TEST_STATION
    return None


async def _ya_post(http, path: str, body: dict) -> dict:
    base = YANDEX_TEST if settings.yandex_delivery_test else YANDEX_PROD
    tok = settings.yandex_delivery_token
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


async def _ya_quote(http, station: str, pkg: dict, value_rub: Decimal,
                    point: str) -> dict | None:
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
            **_span(days, days), "point": point}


async def _ya_quotes(http, origin: dict, dest: dict, pkg: dict,
                     value_rub: Decimal) -> list[dict]:
    """Цена у Яндекса зависит от пункта; для списка вариантов берём
    первый пункт города, при выборе пункта цена пересчитывается."""
    station = _ya_station(origin)
    if not station:
        return []
    if dest.get("point"):
        try:
            return [await _ya_quote(http, station, pkg, value_rub, dest["point"])]
        except httpx.HTTPStatusError:
            return []             # в этот пункт Яндекс не возит
    # Не во все пункты есть доставка — пробуем несколько первых, пока
    # какой-нибудь не ответит ценой
    for p in (await _ya_points(http, dest))[:5]:
        try:
            q = await _ya_quote(http, station, pkg, value_rub, p["code"])
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
    # Тарификатор Почты отвечает с перебоями: на 503 — сразу ещё раз,
    # молчит — ждём 7 секунд, не дольше: дальше выручит недавний расчёт
    for attempt in (1, 2):
        r = await http.get("https://tariff.pochta.ru/v2/calculate/tariff/delivery", params={
            "json": "", "object": 4030, "from": origin["postcode"], "to": dest["postcode"],
            "weight": pkg["weight_g"], "pack": 10}, timeout=7)
        if r.status_code < 500 or attempt == 2:
            break
    r.raise_for_status()
    d = r.json()
    if d.get("errors") or not d.get("paynds"):
        return []
    dl = d.get("delivery") or {}
    return [{"carrier": "pochta", "mode": "post", "tariff": "4030",
             "price": (Decimal(d["paynds"]) / 100).quantize(Decimal("1")),
             **_span(dl.get("min"), dl.get("max"))}]


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


def _span(a, b) -> dict:
    """Срок и строкой, и числами: из чисел складывается срок заказа
    из нескольких посылок."""
    a, b = (a or 0), (b or 0)
    if a or b:
        a = max(a, 1)
        b = max(b, a)
    return {"days": _days(a, b), "days_min": a or None, "days_max": b or None}


# Склонять город для «из …» проще правилом и парой исключений, чем
# словарём: филиалов единицы
_FROM_CITY = {"Ярославль": "Ярославля", "Кемерово": "Кемерово"}


def city_from(city: str | None) -> str:
    """«из Перми», «из Москвы», «из Владивостока» — подпись посылки."""
    c = (city or "").strip()
    if not c:
        return "со склада"
    if c in _FROM_CITY:
        return f"из {_FROM_CITY[c]}"
    if " " in c or "-" in c:
        return f"из г. {c}"
    last = c[-1]
    if last in "ья":
        c = c[:-1] + "и"
    elif last == "а":
        c = c[:-1] + ("и" if c[-2:-1] in "кгхжшщч" else "ы")
    elif last in "бвгджзклмнпрстфхцчшщ":
        c += "а"
    return f"из {c}"


# Где покупатель сам посмотрит, где посылка. У Яндекса страницы по
# номеру нет — ссылку на отслеживание он присылает получателю сам
TRACKING = {"cdek": "https://www.cdek.ru/ru/tracking/?order_id={}",
            "pochta": "https://www.pochta.ru/tracking?barcode={}"}


def track_url(carrier: str | None, number: str | None) -> str | None:
    tpl = TRACKING.get(carrier or "")
    return tpl.format(number) if tpl and number else None


def parcel_labels(origins: list[dict]) -> list[str]:
    """Подписи посылок заказа: «из Перми». Два филиала в одном городе —
    это две посылки с разных складов; их различаем по названию филиала:
    «из Москвы, Ушакова 1»."""
    cities = [o.get("city") for o in origins]
    return [city_from(o.get("city")) + (f", {o['name']}" if cities.count(o.get("city")) > 1
                                        and o.get("name") else "")
            for o in origins]


def title(o: dict) -> str:
    return f"{CARRIERS.get(o['carrier'], o['carrier'])} — {MODES.get(o['mode'], o['mode'])}"


def _issue(carrier: str, origin: dict, dest: dict, pkg: dict) -> str | None:
    """Почему служба не повезёт посылку — чтобы показать плитку с
    причиной, а не прятать вариант молча. None — причины назвать нечего:
    Почте просто нужен индекс, СДЭКу — город из подсказки."""
    frm = origin.get("label") or city_from(origin.get("city"))
    if carrier == "pochta":
        if pkg["weight_g"] > POCHTA_MAX_G:
            return f"Почта принимает посылки до 20 кг — посылка {frm} тяжелее"
        if not origin.get("postcode"):
            return f"Почта не возит {frm}: у филиала не указан индекс"
        if dest.get("postcode"):
            return "Почта не посчитала доставку по этому индексу — проверьте его"
        return None
    if carrier == "yandex":
        if not _ya_station(origin):
            return f"Яндекс не возит {frm}"
        # Тестовая среда везёт всё со своего склада, а не из филиала —
        # называть город филиала тогда неправда
        frm = f" {frm}" if origin.get("yandex_station_id") else ""
        if dest.get("point"):
            return f"Яндекс не возит{frm} в этот пункт — выберите другой"
        return f"Яндекс не возит{frm} в этот город"
    if carrier == "cdek" and dest.get("cdek_code"):
        return f"СДЭК не посчитал доставку {frm}"
    return None


# Службы отвечают с перебоями: тарификатор Почты то отдаёт цену за долю
# секунды, то молчит 20 секунд. Удачный расчёт посылки помним в базе
# (общей для всех процессов сервера): 30 минут берём его сразу — корзина
# посчитала, оформление через минуту берёт ту же цену, не спрашивая службу
# снова; до 6 часов — запасной, если служба не ответила. Цену всё равно
# считал наш сервер, подменить её нельзя
FRESH_S, STALE_S = 1800, 6 * 3600


def _quote_key(name: str, origin: dict, dest: dict, pkg: dict, value_rub: Decimal) -> str:
    return "|".join(str(x) for x in (
        name, origin.get("id"), origin.get("city"), origin.get("postcode"),
        dest.get("city"), dest.get("cdek_code"), dest.get("postcode"), dest.get("point"),
        pkg["weight_g"], pkg["dims"], int(value_rub) if name == "yandex" else ""))


def _to_json(opts: list[dict]) -> str:
    return json.dumps([{**o, "price": str(o["price"])} for o in opts], ensure_ascii=False)


def _from_json(raw) -> list[dict]:
    rows = raw if isinstance(raw, list) else json.loads(raw)
    return [{**o, "price": Decimal(o["price"])} for o in rows]


async def _load_cache(session, keys: list[str]) -> dict:
    if session is None or not keys:
        return {}
    rows = await session.execute(text("""
        SELECT key, options, extract(epoch FROM now() - saved_at) AS age
          FROM delivery_quote_cache WHERE key = ANY(:k)"""), {"k": keys})
    return {r.key: {"options": _from_json(r.options), "age": float(r.age)} for r in rows}


async def _save_cache(session, fresh: dict, commit: bool) -> None:
    if session is None or not fresh:
        return
    for k, opts in fresh.items():
        await session.execute(text("""
            INSERT INTO delivery_quote_cache (key, options, saved_at) VALUES (:k, CAST(:o AS jsonb), now())
            ON CONFLICT (key) DO UPDATE SET options = EXCLUDED.options, saved_at = now()"""),
            {"k": k, "o": _to_json(opts)})
    await session.execute(text("""
        DELETE FROM delivery_quote_cache WHERE saved_at < now() - interval '1 day'"""))
    if commit:
        await session.commit()


async def _parcel_quotes(http, origin: dict, dest: dict, items: list[dict],
                         value_rub: Decimal, cache: dict, fresh: dict) -> dict:
    """Варианты для одной посылки — по всем включённым службам разом:
    покупатель ждёт самую медленную, а не сумму всех. cache — недавние
    расчёты из базы, в fresh кладём новые удачные."""
    pkg = package(items)
    failed: set[str] = set()

    async def one(name, call):
        k = _quote_key(name, origin, dest, pkg, value_rub)
        hit = cache.get(k)
        if hit and hit["age"] < FRESH_S:
            return [dict(o) for o in hit["options"]]
        try:
            got = await call()
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as e:
            stale = hit and hit["age"] < STALE_S
            log.warning("%s: расчёт не удался (%s: %s)%s", name, type(e).__name__, e,
                        " — берём недавний расчёт" if stale else "")
            if stale:
                return [dict(o) for o in hit["options"]]
            failed.add(name)
            return []
        if got:
            fresh[k] = got
        return [dict(o) for o in got]

    calls = {
        "cdek": lambda: _cdek_quotes(http, origin, dest, pkg),
        "yandex": lambda: _ya_quotes(http, origin, dest, pkg, value_rub),
        "pochta": lambda: _pochta_quotes(http, origin, dest, pkg),
    }
    on = [n for n in calls if n in enabled()]
    options, issues = [], {}
    for name, got in zip(on, await asyncio.gather(*(one(n, calls[n]) for n in on))):
        options += got
        if not got:
            # Служба не ответила — так и говорим: «проверьте индекс» здесь неправда
            issues[name] = (f"{CARRIERS[name]} сейчас не отвечает — попробуйте через минуту "
                            "или выберите другую службу") if name in failed else \
                _issue(name, origin, dest, pkg)
    return {"pkg": pkg, "options": options, "issues": issues}


async def quotes(parcels: list[dict], dest: dict, session=None, commit: bool = True) -> dict:
    """Варианты доставки заказа, который едет посылками — по одной из
    каждого филиала.

    parcels: [{origin: {id, city, postcode, cdek_city_code,
    yandex_station_id}, items: [...], value: Decimal}].
    dest: city, cdek_code, postcode, point (пункт Яндекса, если выбран).

    Служба и способ у заказа одни: вариант есть, только если служба
    везёт им каждую посылку. Цена — сумма посылок, срок — от самой
    быстрой до самой долгой. → {options: [...], issues: {служба: причина}}.
    """
    # Недавние расчёты — одним запросом до опроса служб, новые — после:
    # сессию базы нельзя трогать из параллельных задач
    keys = [_quote_key(n, p["origin"], dest, package(p["items"]), p["value"])
            for p in parcels for n in ("cdek", "yandex", "pochta") if n in enabled()]
    cache = await _load_cache(session, keys)
    fresh: dict = {}
    # Все пары «посылка × служба» — тоже разом
    async with httpx.AsyncClient(timeout=10) as http:
        got = await asyncio.gather(*(_parcel_quotes(http, p["origin"], dest, p["items"], p["value"],
                                                    cache, fresh) for p in parcels))
    # commit=False — расчёт внутри чужой транзакции (правка заказа): она
    # и запишет, а предпросмотр откатит
    await _save_cache(session, fresh, commit)

    keys: list[tuple] = []
    for g in got:
        for o in g["options"]:
            if (o["carrier"], o["mode"]) not in keys:
                keys.append((o["carrier"], o["mode"]))

    options = []
    for carrier, mode in keys:
        per = [next((o for o in g["options"] if (o["carrier"], o["mode"]) == (carrier, mode)), None)
               for g in got]
        if None in per:
            continue
        lo = [o["days_min"] for o in per if o.get("days_min")]
        hi = [o["days_max"] for o in per if o.get("days_max")]
        opt = {"carrier": carrier, "mode": mode,
               "tariff": ",".join(dict.fromkeys(o["tariff"] for o in per)),
               "price": sum((o["price"] for o in per), Decimal(0)),
               **_span(min(lo) if lo else 0, max(hi) if hi else 0),
               "from": any(o.get("from") for o in per),
               "estimate": any(o.get("estimate") for o in per),
               "point": per[0].get("point"),
               "parcels": [{"branch_id": p["origin"].get("id"), "city": p["origin"].get("city"),
                            "from_city": p["origin"].get("label") or city_from(p["origin"].get("city")),
                            "part_ids": [i["part_id"] for i in p["items"]],
                            "weight_g": g["pkg"]["weight_g"], "tariff": o["tariff"],
                            "price": o["price"], "days": o["days"],
                            "days_min": o.get("days_min"), "days_max": o.get("days_max")}
                           for p, g, o in zip(parcels, got, per)]}
        opt["title"] = title(opt)
        options.append(opt)

    # Причина — по первой посылке, которую служба не берёт. Берёт все,
    # но разными способами (одну — до пункта, другую — только до двери) —
    # одним способом всё равно не отправить
    issues = {}
    for carrier in enabled():
        if any(o["carrier"] == carrier for o in options):
            continue
        why = next((g["issues"][carrier] for g in got if g["issues"].get(carrier)), None)
        if not why and all(any(o["carrier"] == carrier for o in g["options"]) for g in got):
            why = f"{CARRIERS[carrier]}: посылки не отправить одним способом"
        if why:
            issues[carrier] = why
    return {"options": sorted(options, key=lambda o: o["price"]), "issues": issues}


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
