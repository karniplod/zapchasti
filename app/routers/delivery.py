"""Доставка службами для корзины: подсказка города, варианты с ценой,
пункты выдачи. Вся логика служб — в app/delivery.py; здесь — что везём
(корзина покупателя) и откуда: посылка из каждого филиала, где лежат
детали."""

import time
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import address, delivery
from .. import customer_auth as ca
from ..database import get_session
from .shop import cart_rows, cart_token

router = APIRouter(tags=["delivery"])


async def parcels_of(session: AsyncSession, items: list[dict]) -> list[dict]:
    """Посылки заказа: детали, сложенные по филиалам, где они лежат.
    Филиал берём каждый раз заново из parts — деталь могли перевезти,
    пока она лежала в корзине. Деталь без филиала едет с самой большой
    посылкой. Первой идёт самая большая."""
    items = [i for i in items if i.get("take")]
    groups: dict[int, list] = {}
    for i in items:
        if i.get("branch_id"):
            groups.setdefault(i["branch_id"], []).append(i)
    if not groups:
        return []
    order = sorted(groups, key=lambda b: -sum(i["take"] for i in groups[b]))
    groups[order[0]] += [i for i in items if not i.get("branch_id")]
    rows = await session.execute(text("""
        SELECT id, city, name, postcode, cdek_city_code, yandex_station_id
          FROM branches WHERE id = ANY(:ids)"""), {"ids": order})
    origins = {r.id: dict(r._mapping) for r in rows}
    out = []
    for bid in order:
        if bid not in origins:
            continue
        lines = [{**i, "qty": i["take"]} for i in groups[bid]]
        out.append({
            "origin": origins[bid], "items": lines,
            "value": sum((i["price"] or Decimal(0)) * i["take"] for i in lines),
        })
    for p, label in zip(out, delivery.parcel_labels([p["origin"] for p in out])):
        p["origin"]["label"] = label
    return out


async def remember_cdek_codes(session: AsyncSession, parcels: list[dict]) -> None:
    """Код города СДЭК для филиала узнаём один раз и запоминаем."""
    changed = False
    for p in parcels:
        origin = p["origin"]
        if origin.get("cdek_city_code"):
            changed |= bool((await session.execute(text("""
                UPDATE branches SET cdek_city_code = :c
                 WHERE id = :b AND cdek_city_code IS DISTINCT FROM :c"""),
                {"c": origin["cdek_city_code"], "b": origin["id"]})).rowcount)
    if changed:
        await session.commit()


class Dest(BaseModel):
    city: str = Field(min_length=2, max_length=120)
    cdek_code: int | None = None
    postcode: str | None = Field(default=None, pattern=r"^\d{6}$")
    # Пункт Яндекса: цена у Яндекса зависит от пункта
    point: str | None = Field(default=None, max_length=64)


async def quotes_for_cart(session: AsyncSession, request: Request, customer: dict | None,
                          dest: dict) -> tuple[dict, list[dict]]:
    """→ ({options, issues}, посылки). Варианты — на весь заказ сразу:
    служба и способ у всех посылок одни (см. delivery.quotes)."""
    items = await cart_rows(session, cart_token(request), customer)
    parcels = await parcels_of(session, items)
    if not parcels:
        return {"options": [], "issues": {}}, []
    got = await delivery.quotes(parcels, dest, session)
    await remember_cdek_codes(session, parcels)
    return got, parcels


def parcel_summary(parcels: list[dict]) -> list[dict]:
    """Что едет и откуда — для блока «придёт N посылками» в корзине."""
    return [{
        "branch_id": p["origin"]["id"], "city": p["origin"]["city"],
        "from_city": p["origin"]["label"],
        "count": len(p["items"]), "qty": sum(i["take"] for i in p["items"]),
        "weight_g": delivery.package(p["items"])["weight_g"],
    } for p in parcels]


def _money(o: dict) -> dict:
    return {**o, "price": str(o["price"]),
            "parcels": [{**x, "price": str(x["price"])} for x in o.get("parcels", [])]}


@router.get("/api/delivery/cities")
async def delivery_cities(q: str = ""):
    return await delivery.cities(q)


@router.post("/api/delivery/quotes")
async def delivery_quotes(
    payload: Dest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    customer: dict | None = Depends(ca.optional_customer),
):
    got, parcels = await quotes_for_cart(session, request, customer, payload.model_dump())
    issues = got["issues"]
    return {
        "parcels": parcel_summary(parcels),
        "carriers": delivery.enabled(),
        # Служба не берёт какую-то посылку — плитка с причиной вместо
        # молчания: «Почта: посылка из Москвы тяжелее 20 кг»
        "issues": issues,
        # Почта считает только по индексу — без него её вариантов нет
        "need_postcode": "pochta" in delivery.enabled() and not payload.postcode
                         and not issues.get("pochta") and bool(parcels),
        "options": [_money(o) for o in got["options"]],
        # Службы, которые сейчас не ответили, — повторить расчёт можно
        "retry": got.get("failed", []),
    }


@router.get("/api/delivery/points")
async def delivery_points(carrier: str, city: str = "", cdek_code: int | None = None):
    if carrier not in delivery.CARRIERS:
        raise HTTPException(404)
    return await delivery.points(carrier, {"city": city, "cdek_code": cdek_code})


# Подсказки открыты всем, а у DaData дневной лимит: один адрес — не
# больше 60 запросов в минуту, человеку при наборе адреса хватает с запасом
_hits: dict[str, list[float]] = {}


def _too_often(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _hits.get(ip, []) if now - t < 60]
    hits.append(now)
    _hits[ip] = hits
    if len(_hits) > 5000:
        _hits.clear()
    return len(hits) > 60


@router.get("/api/address/suggest")
async def address_suggest(request: Request, level: str, q: str = "", city: str = "",
                          street_fias: str = "", country: str = "RU"):
    """Подсказки улицы и дома — прокси к DaData: ключ остаётся на сервере."""
    if level not in ("street", "house") or len(q) > 120:
        raise HTTPException(422, "Неверный запрос подсказки")
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "?"))
    if _too_often(ip):
        raise HTTPException(429, "Слишком часто — подождите минуту")
    return await address.suggest(level, q, city[:120], street_fias[:64], country[:2])
