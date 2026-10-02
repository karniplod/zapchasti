"""Сводка бэкенда — точка входа после логина."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, require_role
from ..database import get_session
from ..services import oem as oem_service
from ..templating import templates

router = APIRouter(tags=["admin"])


async def summary_counts(session: AsyncSession) -> dict:
    """Цифры сводки — и для страницы /admin, и для приложения сотрудника."""
    # Одним запросом — иначе полтора десятка round-trip'ов на открытие
    row = (
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
    return dict(row)


@router.get("/admin", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: dict = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    s = await summary_counts(session)

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


@router.get("/api/admin/summary")
async def summary_api(
    user: dict = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Сводка для приложения: что требует внимания сегодня. Те же цифры,
    что на /admin, — чтобы телефон и сайт не расходились."""
    return await summary_counts(session)


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


# ------------------------------------------------------------------
# Журналы запросов: VIN, поиск по названию, подбор через каталог
# ------------------------------------------------------------------
# Сводка «что не нашли» отвечает на вопрос «что купить», но не показывает
# сами запросы. Здесь — каждый запрос целиком: какой VIN, нашлось ли,
# кто искал. Все три журнала отдаются страницами, с периодом и фильтром
# «нашлось / не нашлось».

RESOLUTIONS = {
    "exact": "машина определена",
    "brand_year": "только марка и год",
    "unknown": "VIN не распознан",
}

CUSTOMER_LABEL = "coalesce(cu.name, cu.phone, cu.email)"


def _period(alias: str) -> str:
    """За сколько дней; 0 — за всё время."""
    return f"(:days = 0 OR {alias}.created_at > now() - make_interval(days => :days))"


def _found(alias: str, found: str | None) -> str:
    return {"yes": f"{alias}.results_count > 0",
            "no": f"{alias}.results_count = 0"}.get(found or "", "true")


def _page(rows) -> tuple[int, list[dict]]:
    """Общее число строк приходит в каждой строке (count(*) OVER) —
    забираем его и убираем из самих строк."""
    out = [dict(r._mapping) for r in rows]
    total = out[0]["total_rows"] if out else 0
    for r in out:
        r.pop("total_rows")
    return total, out


@router.get("/api/reports/vin")
async def report_vin(
    days: int = Query(30, ge=0, le=3650),
    found: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    """Каждый VIN, который вводили, — полностью, а не сводкой по заводу."""
    p = {"days": days, "lim": limit, "off": offset}
    summary = (await session.execute(text(f"""
        SELECT count(*)                                    AS total,
               count(*) FILTER (WHERE q.results_count > 0) AS found,
               count(*) FILTER (WHERE q.results_count = 0) AS not_found,
               count(DISTINCT q.vin)                       AS unique_vins
          FROM vin_queries q WHERE {_period('q')}"""), p)).mappings().first()
    total, rows = _page(await session.execute(text(f"""
        SELECT count(*) OVER () AS total_rows,
               q.created_at, q.vin, q.wmi, w.manufacturer, q.resolution,
               b.name || ' ' || m.name || ' ' || g.name AS car,
               q.results_count, {CUSTOMER_LABEL} AS customer,
               -- Сколько раз этот VIN вводили за всё время: повтор — это
               -- человек, который ждёт деталь, а не случайный прохожий
               (SELECT count(*) FROM vin_queries q2 WHERE q2.vin = q.vin) AS times
          FROM vin_queries q
          LEFT JOIN wmi w         ON w.code = q.wmi
          LEFT JOIN generations g ON g.id = q.generation_id
          LEFT JOIN models m      ON m.id = g.model_id
          LEFT JOIN brands b      ON b.id = m.brand_id
          LEFT JOIN customers cu  ON cu.id = q.customer_id
         WHERE {_period('q')} AND {_found('q', found)}
         ORDER BY q.created_at DESC LIMIT :lim OFFSET :off"""), p))
    for r in rows:
        r["resolution_label"] = RESOLUTIONS.get(r["resolution"], r["resolution"])
    return {"summary": dict(summary), "total": total, "rows": rows}


@router.get("/api/reports/searches")
async def report_searches(
    days: int = Query(30, ge=0, le=3650),
    found: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    """Поиск по названию и номеру: что вводили и сколько нашлось. Сверху —
    частые запросы: десять «рулевая рейка» без результата важнее одной."""
    where = f"{_period('s')} AND {_found('s', found)}"
    p = {"days": days, "lim": limit, "off": offset}
    summary = (await session.execute(text(f"""
        SELECT count(*)                                    AS total,
               count(*) FILTER (WHERE s.results_count > 0) AS found,
               count(*) FILTER (WHERE s.results_count = 0) AS not_found,
               count(DISTINCT lower(s.query))              AS unique_queries
          FROM search_queries s WHERE {_period('s')}"""), p)).mappings().first()
    top = await session.execute(text(f"""
        SELECT min(s.query) AS query, count(*) AS times,
               count(*) FILTER (WHERE s.results_count = 0) AS empty,
               max(s.results_count) AS best
          FROM search_queries s WHERE {where}
         GROUP BY lower(s.query)
         ORDER BY count(*) DESC, max(s.created_at) DESC LIMIT 15"""), p)
    total, rows = _page(await session.execute(text(f"""
        SELECT count(*) OVER () AS total_rows,
               s.created_at, s.query, s.results_count, s.city,
               {CUSTOMER_LABEL} AS customer
          FROM search_queries s
          LEFT JOIN customers cu ON cu.id = s.customer_id
         WHERE {where}
         ORDER BY s.created_at DESC LIMIT :lim OFFSET :off"""), p))
    return {"summary": dict(summary), "top": [dict(r._mapping) for r in top],
            "total": total, "rows": rows}


@router.get("/api/reports/browses")
async def report_browses(
    days: int = Query(30, ge=0, le=3650),
    found: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: dict = Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    """Подбор через каталог: узел, состояние, цена, машина, город."""
    where = f"{_period('cb')} AND {_found('cb', found)}"
    p = {"days": days, "lim": limit, "off": offset}
    summary = (await session.execute(text(f"""
        SELECT count(*)                                     AS total,
               count(*) FILTER (WHERE cb.results_count > 0) AS found,
               count(*) FILTER (WHERE cb.results_count = 0) AS not_found,
               count(*) FILTER (WHERE cb.category_id IS NOT NULL) AS with_node
          FROM catalog_browses cb WHERE {_period('cb')}"""), p)).mappings().first()
    # Какие узлы открывают чаще и как часто там пусто
    top = await session.execute(text(f"""
        SELECT coalesce(par.name || ' / ', '') || c.name AS node,
               count(*) AS times,
               count(*) FILTER (WHERE cb.results_count = 0) AS empty
          FROM catalog_browses cb
          JOIN part_categories c        ON c.id = cb.category_id
          LEFT JOIN part_categories par ON par.id = c.parent_id
         WHERE {where}
         GROUP BY par.name, c.name
         ORDER BY count(*) DESC LIMIT 15"""), p)
    conds = await session.execute(text(f"""
        SELECT x.cond AS condition, count(*) AS times
          FROM catalog_browses cb, unnest(cb.conditions) AS x(cond)
         WHERE {where}
         GROUP BY x.cond ORDER BY x.cond"""), p)
    total, rows = _page(await session.execute(text(f"""
        SELECT count(*) OVER () AS total_rows,
               cb.created_at, cb.conditions, cb.price_min, cb.price_max,
               cb.city, cb.results_count,
               c.name AS category, par.name AS node,
               b.name || ' ' || m.name || ' ' || g.name AS car,
               {CUSTOMER_LABEL} AS customer
          FROM catalog_browses cb
          LEFT JOIN part_categories c   ON c.id = cb.category_id
          LEFT JOIN part_categories par ON par.id = c.parent_id
          LEFT JOIN generations g       ON g.id = cb.generation_id
          LEFT JOIN models m            ON m.id = g.model_id
          LEFT JOIN brands b            ON b.id = m.brand_id
          LEFT JOIN customers cu        ON cu.id = cb.customer_id
         WHERE {where}
         ORDER BY cb.created_at DESC LIMIT :lim OFFSET :off"""), p))
    return {"summary": dict(summary), "top": [dict(r._mapping) for r in top],
            "conditions": [dict(r._mapping) for r in conds],
            "total": total, "rows": rows}
