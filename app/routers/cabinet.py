"""Личный кабинет покупателя: бонусы и скидки, профиль, адреса, купленное.

Заказы — app/routers/shop.py (список, страница заказа) и
app/routers/account_orders.py (правка, оплата, чек). Правила баллов и
скидок — app/loyalty.py.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .. import customer_auth as ca
from .. import loyalty
from ..database import get_session
from .shop import cart_rows, cart_token

router = APIRouter(tags=["cabinet"])


# ------------------------------------------------------------------
# Бонусы и скидки при оформлении
# ------------------------------------------------------------------


@router.get("/api/account/loyalty")
async def account_loyalty(session: AsyncSession = Depends(get_session),
                          customer: dict = Depends(ca.current_customer)):
    """Баланс баллов и персональная скидка — для оформления заказа."""
    disc = await loyalty.best_discount(session, customer, Decimal(100))
    return {"balance": await loyalty.balance(session, customer["id"]),
            "personal_discount": str(disc["personal"]),
            "accrual_percent": loyalty.ACCRUAL_PERCENT,
            "spend_limit_percent": loyalty.SPEND_LIMIT_PERCENT}


class PromoIn(BaseModel):
    code: str = Field(default="", max_length=40)


@router.post("/api/cart/promo")
async def cart_promo(payload: PromoIn, request: Request,
                     session: AsyncSession = Depends(get_session),
                     customer: dict | None = Depends(ca.optional_customer)):
    """Скидка по корзине — с промокодом или без: оформление показывает
    её до отправки заказа. Сервер при оформлении считает заново."""
    items = await cart_rows(session, cart_token(request), customer)
    goods = sum(((i["price"] or 0) * i["take"] for i in items), Decimal(0))
    d = await loyalty.best_discount(session, customer, goods, payload.code)
    cap = loyalty.bonus_cap(goods - d["amount"])
    bal = await loyalty.balance(session, customer["id"]) if customer else 0
    return {
        "goods": str(goods), "discount": str(d["amount"]), "source": d["source"],
        "kind": d["kind"], "value": str(d["value"]) if d["value"] is not None else None,
        "code": payload.code.strip().upper() if d["source"] == "promo" else None,
        "error": d["promo_error"], "note": d["promo_note"],
        "bonus_balance": bal, "bonus_max": min(cap, bal),
    }
