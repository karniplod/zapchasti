"""Доставка службами для корзины: подсказка города, варианты с ценой,
пункты выдачи. Вся логика служб — в app/delivery.py; здесь — откуда
везём (филиал) и что везём (корзина покупателя)."""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import customer_auth as ca
from .. import delivery
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
    opts = await delivery.quotes(origin, dest, [{**i, "qty": i["take"]} for i in items], value)
    await remember_cdek_code(session, origin)
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
        # Почта считает только по индексу — без него её вариантов нет
        "need_postcode": "pochta" in delivery.enabled() and not payload.postcode,
        "options": [{**o, "price": str(o["price"])} for o in opts],
    }


@router.get("/api/delivery/points")
async def delivery_points(carrier: str, city: str = "", cdek_code: int | None = None):
    if carrier not in delivery.CARRIERS:
        raise HTTPException(404)
    return await delivery.points(carrier, {"city": city, "cdek_code": cdek_code})
