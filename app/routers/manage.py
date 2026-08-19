"""Управление складом: список машин и таблица деталей.

Всё, что создано в приёмке и разборе, правится отсюда: цена, состояние,
место, публикация. Без этого экрана любая опечатка остаётся навсегда.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, require_role
from ..database import get_session

router = APIRouter(tags=["manage"])
templates = Jinja2Templates(directory="templates")

STATUSES = ["draft", "in_stock", "reserved", "sold", "written_off"]


@router.get("/donors", response_class=HTMLResponse)
async def donors_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse("admin/donors.html", {"request": request, "user": user})


@router.get("/parts", response_class=HTMLResponse)
async def parts_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse("admin/parts.html", {"request": request, "user": user})


@router.get("/api/manage/donors")
async def donors_list(
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    rows = await session.execute(
        text("""
        SELECT d.id, d.code, d.vin, d.year, d.color, d.status::text AS status,
               d.accepted_at, d.purchase_price,
               b.name AS brand, m.name AS model, g.name AS generation,
               (SELECT count(*) FROM parts p WHERE p.donor_id = d.id) AS parts,
               (SELECT count(*) FROM parts p
                 WHERE p.donor_id = d.id AND p.status = 'sold')        AS sold,
               (SELECT coalesce(sum(oi.price), 0) FROM order_items oi
                  JOIN parts p ON p.id = oi.part_id
                 WHERE p.donor_id = d.id)                              AS revenue
          FROM donors d
          JOIN generations g ON g.id = d.generation_id
          JOIN models m      ON m.id = g.model_id
          JOIN brands b      ON b.id = m.brand_id
         WHERE (CAST(:st AS text) IS NULL OR d.status::text = CAST(:st AS text))
         ORDER BY d.id DESC LIMIT 200
    """),
        {"st": status},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/api/manage/parts")
async def parts_list(
    q: str | None = None,
    status: str | None = None,
    donor_id: int | None = None,
    problems: bool = False,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    rows = await session.execute(
        text("""
        SELECT p.id, p.sku, p.name, p.condition::text AS condition, p.price,
               p.status::text AS status, p.location, p.published, p.oem_number,
               p.source, c.name AS category, d.code AS donor_code,
               b.name AS brand, m.name AS model,
               (SELECT count(*) FROM part_photos ph WHERE ph.part_id = p.id) AS photos,
               (SELECT count(*) FROM part_applicability pa WHERE pa.part_id = p.id) AS fits
          FROM parts p
          JOIN part_categories c  ON c.id = p.category_id
          LEFT JOIN donors d      ON d.id = p.donor_id
          LEFT JOIN generations g ON g.id = d.generation_id
          LEFT JOIN models m      ON m.id = g.model_id
          LEFT JOIN brands b      ON b.id = m.brand_id
         WHERE (CAST(:q AS text) IS NULL
                OR p.name ILIKE '%' || CAST(:q AS text) || '%'
                OR p.sku  ILIKE '%' || CAST(:q AS text) || '%'
                OR p.oem_number ILIKE '%' || CAST(:q AS text) || '%')
           AND (CAST(:st AS text) IS NULL OR p.status::text = CAST(:st AS text))
           AND (CAST(:d AS int) IS NULL OR p.donor_id = CAST(:d AS int))
           AND (NOT CAST(:pr AS boolean) OR p.price IS NULL
                OR p.status = 'draft'
                OR NOT EXISTS (SELECT 1 FROM part_photos ph WHERE ph.part_id = p.id))
         ORDER BY p.id DESC LIMIT 300
    """),
        {"q": q, "st": status, "d": donor_id, "pr": problems},
    )
    return [dict(r._mapping) for r in rows]


class PartPatch(BaseModel):
    price: Decimal | None = None
    condition: str | None = None
    location: str | None = None
    status: str | None = None
    published: bool | None = None


@router.patch("/api/manage/parts/{part_id}")
async def patch_part(
    part_id: int,
    payload: PartPatch,
    user=Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    cur = (
        await session.execute(
            text("SELECT status::text AS status FROM parts WHERE id = :id"),
            {"id": part_id},
        )
    ).first()
    if not cur:
        raise HTTPException(404, "Деталь не найдена")
    if cur.status == "sold" and payload.status not in (None, "sold"):
        raise HTTPException(409, "Проданную деталь менять нельзя — оформите возврат")

    sets, params = [], {"id": part_id}
    if payload.price is not None:
        sets.append("price = :price")
        params["price"] = payload.price
    if payload.condition:
        if payload.condition not in {"A", "B", "C", "D"}:
            raise HTTPException(422, "Состояние должно быть A, B, C или D")
        sets.append("condition = CAST(:cond AS part_condition)")
        params["cond"] = payload.condition
    if payload.location is not None:
        sets.append("location = :loc")
        params["loc"] = payload.location
    if payload.status:
        if payload.status not in STATUSES:
            raise HTTPException(422, "Неизвестный статус")
        sets.append("status = CAST(:st AS part_status)")
        params["st"] = payload.status
    if payload.published is not None:
        sets.append("published = :pub")
        params["pub"] = payload.published
    if not sets:
        raise HTTPException(422, "Нечего менять")

    await session.execute(text(f"UPDATE parts SET {', '.join(sets)} WHERE id = :id"), params)
    await session.commit()
    return {"ok": True}


@router.delete("/api/manage/parts/{part_id}", status_code=204)
async def delete_part(
    part_id: int,
    user=Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    """Удалять можно только то, что не продано: проданная деталь —
    это история сделки, её списывают, а не стирают."""
    row = (
        await session.execute(
            text("SELECT sku, status::text AS status FROM parts WHERE id = :id"),
            {"id": part_id},
        )
    ).first()
    if not row:
        raise HTTPException(404, "Деталь не найдена")
    if row.status == "sold":
        raise HTTPException(409, "Проданную деталь нельзя удалить — спишите её")

    in_order = (
        await session.execute(
            text("SELECT 1 FROM order_items WHERE part_id = :id LIMIT 1"),
            {"id": part_id},
        )
    ).first()
    if in_order:
        raise HTTPException(409, "Деталь есть в заказе — сначала отмените заказ")

    await session.execute(text("DELETE FROM parts WHERE id = :id"), {"id": part_id})
    await session.commit()

    # Фото убираем после удаления записи: если удаление не прошло,
    # снимки останутся на месте
    import shutil
    from pathlib import Path as P

    shutil.rmtree(P("media/parts") / str(part_id), ignore_errors=True)
