"""Доставка службами для корзины: подсказка города, варианты с ценой,
пункты выдачи. Вся логика служб — в app/delivery.py; здесь — откуда
везём (филиал) и что везём (корзина покупателя)."""

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


async def origin_of(session: AsyncSession, items: list[dict]) -> dict | None:
    """Откуда отправляем: филиал, где лежит больше всего деталей заказа —
    туда же по умолчанию предлагается самовывоз."""
    here = [i["branch_id"] for i in items if i.get("branch_id") and i.get("take")]
    if not here:
        return None
    bid = max(set(here), key=here.count)
    row = (await session.execute(text("""
        SELECT id, city, postcode, cdek_city_code FROM branches WHERE id = :b"""),
        {"b": bid})).first()
    return dict(row._mapping) if row else None


async def remember_cdek_code(session: AsyncSession, origin: dict) -> None:
    """Код города СДЭК для филиала узнаём один раз и запоминаем."""
    if origin.get("cdek_city_code"):
        await session.execute(text("""
            UPDATE branches SET cdek_city_code = :c
             WHERE id = :b AND cdek_city_code IS DISTINCT FROM :c"""),
            {"c": origin["cdek_city_code"], "b": origin["id"]})
        await session.commit()


class Dest(BaseModel):
    city: str = Field(min_length=2, max_length=120)
    cdek_code: int | None = None
    postcode: str | None = Field(default=None, pattern=r"^\d{6}$")
    # Пункт Яндекса: цена у Яндекса зависит от пункта
    point: str | None = Field(default=None, max_length=64)


async def quotes_for_cart(session: AsyncSession, request: Request, customer: dict | None,
                          dest: dict) -> tuple[list[dict], dict | None]:
    items = [i for i in await cart_rows(session, cart_token(request), customer) if i["take"]]
    if not items:
        return [], None
    origin = await origin_of(session, items)
    if not origin:
        return [], None
    value = sum((i["price"] or Decimal(0)) * i["take"] for i in items)
    parcel = [{**i, "qty": i["take"]} for i in items]
    opts = await delivery.quotes(origin, dest, parcel, value)
    await remember_cdek_code(session, origin)
    # Почему нет Почты — для плитки с причиной (см. delivery.pochta_issue)
    origin["pochta_issue"] = delivery.pochta_issue(
        parcel, dest.get("postcode"), any(o["carrier"] == "pochta" for o in opts))
    return opts, origin


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
    opts, origin = await quotes_for_cart(session, request, customer, payload.model_dump())
    return {
        "from": origin["city"] if origin else None,
        "carriers": delivery.enabled(),
        # Почта считает только по индексу — без него её вариантов нет.
        # Не подходит по весу или индексу — причина вместо молчания
        "pochta_issue": origin and origin.get("pochta_issue"),
        "need_postcode": "pochta" in delivery.enabled() and not payload.postcode
                         and not (origin and origin.get("pochta_issue")),
        "options": [{**o, "price": str(o["price"])} for o in opts],
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
