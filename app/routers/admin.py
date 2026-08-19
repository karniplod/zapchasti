"""Сводка бэкенда — точка входа после логина."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user
from ..database import get_session
from ..templating import templates

router = APIRouter(tags=["admin"])


@router.get("/admin", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: dict = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    # Одним запросом — иначе полтора десятка round-trip'ов на открытие
    s = (
        (
            await session.execute(
                text("""
        SELECT
          (SELECT count(*) FROM brands)                              AS brands,
          (SELECT count(*) FROM part_categories)                     AS categories,
          (SELECT count(*) FROM donors)                              AS donors,
          (SELECT count(*) FROM parts WHERE status = 'in_stock')     AS in_stock,
          (SELECT count(*) FROM parts WHERE status = 'draft')        AS drafts,
          (SELECT count(*) FROM parts
            WHERE status = 'in_stock' AND price IS NULL)             AS no_price,
          (SELECT count(*) FROM generations
            WHERE needs_review AND source = 'manual')            AS to_review,
          (SELECT count(*) FROM leads WHERE NOT processed)           AS leads
    """)
            )
        )
        .mappings()
        .first()
    )

    active = [
        dict(r)
        for r in (
            await session.execute(
                text("""
        SELECT d.id, d.code, d.status::text AS status, d.year,
               b.name AS brand, m.name AS model, g.name AS generation,
               (SELECT count(*) FROM parts p WHERE p.donor_id = d.id) AS parts
          FROM donors d
          JOIN generations g ON g.id = d.generation_id
          JOIN models m      ON m.id = g.model_id
          JOIN brands b      ON b.id = m.brand_id
         WHERE d.status IN ('accepted', 'dismantling')
         ORDER BY d.id DESC LIMIT 8
    """)
            )
        ).mappings()
    ]

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "s": s,
            "active": active,
            "empty": s["brands"] == 0 or s["categories"] == 0,
        },
    )
