"""Сводка бэкенда — точка входа после логина."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, require_role
from ..database import get_session
from ..services import oem as oem_service
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
          -- Лежит на складе, но покупатель её не видит
          (SELECT count(*) FROM parts
            WHERE status = 'in_stock' AND NOT published)             AS unpublished,
          -- Без номера деталь не найти поиском по каталогу
          (SELECT count(*) FROM parts
            WHERE status = 'in_stock' AND oem_number IS NULL)        AS no_number,
          -- Номер есть, но с деталью его никто не сверил
          (SELECT count(*) FROM parts
            WHERE status = 'in_stock' AND oem_number IS NOT NULL
              AND NOT oem_verified)                                  AS unverified,
          (SELECT count(*) FROM orders WHERE status = 'new')         AS new_orders,
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
               (SELECT count(*) FROM parts p WHERE p.donor_id = d.id) AS parts,
               -- Миниатюра, если её сделали при загрузке; у старых
               -- снимков её нет, и тогда берём сам файл
               (SELECT coalesce(ph.thumb, ph.path) FROM donor_photos ph
                 WHERE ph.donor_id = d.id
                 ORDER BY ph.sort_order LIMIT 1) AS photo
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


# ------------------------------------------------------------------
# Отчёты
# ------------------------------------------------------------------
# Оба отчёта считались с самого начала, но смотреть их было негде:
# ручки были, интерфейса не было. Отчёт, который никто не видит,
# всё равно что отсутствует.


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    user: dict = Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    """Две вещи, на которые смотрят, когда решают, куда вкладываться:
    чего не хватает на складе и стоит ли платить за каталог номеров."""
    demand = [
        dict(r._mapping)
        for r in await session.execute(text("SELECT * FROM unmet_demand LIMIT 50"))
    ]

    accuracy = await oem_service.accuracy(session)

    # Сколько раз подсказка вообще срабатывала и сколько номеров
    # в итоге ввели руками — без этого проценты не с чем сравнить
    totals = (
        await session.execute(
            text("""
        SELECT count(*) FILTER (WHERE oem_source = 'manual')            AS manual,
               count(*) FILTER (WHERE oem_source IS NOT NULL
                                  AND oem_source <> 'manual')           AS hinted,
               count(*) FILTER (WHERE oem_number IS NOT NULL)           AS with_number,
               count(*)                                                 AS total
          FROM parts
    """)
        )
    ).mappings().first()

    return templates.TemplateResponse(
        "admin/reports.html",
        {
            "request": request,
            "user": user,
            "demand": demand,
            "accuracy": accuracy,
            "totals": totals,
        },
    )
