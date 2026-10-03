"""Бэкенд: промокоды, персональные скидки и баллы покупателей.

Промокод заводит менеджер: процент или сумма, срок, лимит заказов,
минимальная сумма, «один раз на покупателя». Персональную скидку и
ручное начисление баллов — в карточке заказа, блок «Покупатель».
Правила применения — app/loyalty.py.
"""

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import loyalty
from ..auth import current_user, require_role
from ..database import get_session
from ..templating import templates
from ..validation import promo as rules
from ..validation import require

router = APIRouter(tags=["promo"])


@router.get("/promo", response_class=HTMLResponse)
async def promo_page(request: Request, user=Depends(require_role("manager"))):
    return templates.TemplateResponse("admin/promo.html", {"request": request, "user": user})


@router.get("/api/manage/promo")
async def promo_list(session: AsyncSession = Depends(get_session),
                     user=Depends(require_role("manager"))):
    rows = await session.execute(text("""
        SELECT p.*,
               (SELECT count(*) FROM orders o WHERE o.promo_code_id = p.id
                   AND o.status <> 'cancelled') AS uses,
               (SELECT coalesce(sum(o.discount_amount), 0) FROM orders o
                 WHERE o.promo_code_id = p.id AND o.status <> 'cancelled') AS given
          FROM promo_codes p ORDER BY p.active DESC, p.created_at DESC"""))
    today = date.today()
    out = []
    for r in rows:
        d = dict(r._mapping)
        d["state"] = ("выключен" if not r.active else
                      "ещё не начался" if r.starts_at and today < r.starts_at else
                      "закончился" if r.ends_at and today > r.ends_at else
                      "израсходован" if r.max_uses is not None and r.uses >= r.max_uses else
                      "действует")
        out.append(d)
    return out


class PromoIn(BaseModel):
    code: str = Field(max_length=40)
    kind: str = Field(max_length=10)
    value: Decimal = Field(gt=0, le=1_000_000)
    min_total: Decimal = Field(default=Decimal(0), ge=0, le=100_000_000)
    starts_at: date | None = None
    ends_at: date | None = None
    max_uses: int | None = Field(default=None, ge=1, le=1_000_000)
    once_per_customer: bool = True
    active: bool = True
    comment: str | None = Field(default=None, max_length=500)


def _checked(p: PromoIn) -> str:
    code, err = rules.check_code(p.code)
    require(err)
    require(rules.check_value(p.kind, p.value))
    require(rules.check_dates(p.starts_at, p.ends_at))
    return code


@router.post("/api/manage/promo", status_code=201)
async def promo_create(payload: PromoIn, session: AsyncSession = Depends(get_session),
                       user=Depends(require_role("manager"))):
    code = _checked(payload)
    if (await session.execute(text("SELECT 1 FROM promo_codes WHERE code = :c"), {"c": code})).first():
        raise HTTPException(409, f"Промокод {code} уже есть")
    pid = (await session.execute(text("""
        INSERT INTO promo_codes (code, kind, value, min_total, starts_at, ends_at, max_uses,
                                 once_per_customer, active, comment, created_by)
        VALUES (:code, :kind, :value, :min_total, :starts_at, :ends_at, :max_uses,
                :once_per_customer, :active, :comment, :u) RETURNING id"""),
        {**payload.model_dump(), "code": code, "comment": (payload.comment or "").strip() or None,
         "u": user["id"]})).scalar_one()
    await session.commit()
    return {"id": pid}


@router.put("/api/manage/promo/{promo_id}")
async def promo_update(promo_id: int, payload: PromoIn, session: AsyncSession = Depends(get_session),
                       user=Depends(require_role("manager"))):
    code = _checked(payload)
    if (await session.execute(text("SELECT 1 FROM promo_codes WHERE code = :c AND id <> :id"),
                              {"c": code, "id": promo_id})).first():
        raise HTTPException(409, f"Промокод {code} уже есть")
    n = (await session.execute(text("""
        UPDATE promo_codes SET code = :code, kind = :kind, value = :value, min_total = :min_total,
               starts_at = :starts_at, ends_at = :ends_at, max_uses = :max_uses,
               once_per_customer = :once_per_customer, active = :active, comment = :comment
         WHERE id = :id"""),
        {**payload.model_dump(), "code": code, "comment": (payload.comment or "").strip() or None,
         "id": promo_id})).rowcount
    if not n:
        raise HTTPException(404, "Промокод не найден")
    await session.commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Покупатель: персональная скидка и баллы
# ------------------------------------------------------------------


class CustomerLoyalty(BaseModel):
    personal_discount: Decimal | None = Field(default=None, ge=0, le=50)
    bonus_add: int | None = Field(default=None, ge=-1_000_000, le=1_000_000)
    comment: str | None = Field(default=None, max_length=300)


@router.get("/api/manage/customers/{customer_id}/loyalty")
async def customer_loyalty(customer_id: int, session: AsyncSession = Depends(get_session),
                           user=Depends(current_user)):
    c = (await session.execute(text("SELECT personal_discount FROM customers WHERE id = :c"),
                               {"c": customer_id})).first()
    if not c:
        raise HTTPException(404, "Покупатель не найден")
    history = [dict(r._mapping) for r in await session.execute(text("""
        SELECT b.amount, b.kind, b.comment, b.created_at, coalesce(u.full_name, u.login) AS who
          FROM bonus_ledger b LEFT JOIN users u ON u.id = b.user_id
         WHERE b.customer_id = :c ORDER BY b.created_at DESC LIMIT 20"""), {"c": customer_id})]
    return {"balance": await loyalty.balance(session, customer_id),
            "personal_discount": str(c.personal_discount), "history": history}


@router.patch("/api/manage/customers/{customer_id}/loyalty")
async def customer_loyalty_patch(customer_id: int, payload: CustomerLoyalty,
                                 session: AsyncSession = Depends(get_session),
                                 user=Depends(require_role("manager"))):
    if not (await session.execute(text("SELECT 1 FROM customers WHERE id = :c"),
                                  {"c": customer_id})).first():
        raise HTTPException(404, "Покупатель не найден")
    if payload.personal_discount is not None:
        require(rules.check_personal(payload.personal_discount))
        await session.execute(text("UPDATE customers SET personal_discount = :d WHERE id = :c"),
                              {"d": payload.personal_discount, "c": customer_id})
    if payload.bonus_add:
        comment = (payload.comment or "").strip()
        if len(comment) < 3:
            raise HTTPException(422, "Напишите, за что начисляете или списываете баллы")
        if payload.bonus_add < 0 and await loyalty.balance(session, customer_id) + payload.bonus_add < 0:
            raise HTTPException(409, "Столько баллов на счету нет")
        await loyalty.move(session, customer_id, payload.bonus_add, "manual", comment, user=user)
    await session.commit()
    return {"ok": True, "balance": await loyalty.balance(session, customer_id)}
