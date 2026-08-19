"""
Публичный каталог запчастей.

Вход — VIN покупателя. Логика с деградацией: чем меньше система знает
о машине, тем больше выбирает сам покупатель, но пустой выдачи не бывает.

  exact       паттерн известен -> поколение и модификация определены
  brand_year  знаком только завод -> предлагаем модели этой марки за этот год
  unknown     WMI незнаком -> обычный фильтр, VIN всё равно записываем

Каждый запрос пишется в vin_queries: нулевая выдача — это заявка
на закупку следующей машины.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import optional_user
from ..database import get_session
from ..templating import templates
from ..vin_decoder import decode

router = APIRouter(tags=["catalog"])

PAGE_SIZE = 24

CONDITION_LABELS = {
    "A": "Отличное",
    "B": "Рабочее",
    "C": "С дефектом",
    "D": "Под восстановление",
}


# ------------------------------------------------------------------
# Страницы
# ------------------------------------------------------------------


@router.get("/catalog", response_class=HTMLResponse)
async def catalog_page(request: Request, session: AsyncSession = Depends(get_session)):
    # Сотруднику показываем ссылку в бэкенд, покупателю — нет
    user = await optional_user(request, session)
    return templates.TemplateResponse("catalog.html", {"request": request, "user": user})


@router.get("/p/{sku}", response_class=HTMLResponse)
async def part_page(sku: str, request: Request, session: AsyncSession = Depends(get_session)):
    """Сюда ведёт QR с этикетки — и для кладовщика, и для покупателя."""
    part = (
        await session.execute(
            text("""
        SELECT p.id, p.sku, p.name, p.oem_number, p.condition::text AS condition,
               p.condition_note, p.price, p.status::text AS status, p.weight_kg,
               c.name AS category,
               d.code AS donor_code, d.year, d.color, d.mileage_km,
               b.name AS brand, m.name AS model, g.name AS generation, g.body_type,
               g.id AS generation_id,
               concat_ws(' ', mo.engine_volume || ' л', mo.engine_code,
                         mo.transmission, mo.drive) AS modification
          FROM parts p
          JOIN part_categories c ON c.id = p.category_id
          LEFT JOIN donors d          ON d.id = p.donor_id
          LEFT JOIN generations g ON g.id = d.generation_id
          LEFT JOIN models m      ON m.id = g.model_id
          LEFT JOIN brands b      ON b.id = m.brand_id
          LEFT JOIN modifications mo ON mo.id = d.modification_id
         WHERE p.sku = :sku
    """),
            {"sku": sku},
        )
    ).first()

    if not part:
        raise HTTPException(404, "Деталь не найдена или снята с продажи")

    photos = [
        r.path
        for r in await session.execute(
            text("""
        SELECT path FROM part_photos WHERE part_id = :id ORDER BY sort_order
    """),
            {"id": part.id},
        )
    ]

    # На какие ещё машины встанет эта деталь
    fits = []

    # Деталь без донора ищется только по ручной применимости —
    # без неё покупатель её не найдёт вообще
    if not part.donor_code:
        fits = [
            dict(r._mapping)
            for r in await session.execute(
                text("""
            SELECT b.name AS brand, m.name AS model, g.name AS generation,
                   g.year_from, g.year_to
              FROM part_applicability pa
              JOIN generations g ON g.id = pa.generation_id
              JOIN models m      ON m.id = g.model_id
              JOIN brands b      ON b.id = m.brand_id
             WHERE pa.part_id = :id
             ORDER BY b.name, m.name
        """),
                {"id": part.id},
            )
        ]

    if not fits and part.oem_number:
        fits = [
            dict(r._mapping)
            for r in await session.execute(
                text("""
            SELECT DISTINCT b.name AS brand, m.name AS model, g.name AS generation,
                   g.year_from, g.year_to
              FROM oem_applicability oa
              JOIN generations g ON g.id = oa.generation_id
              JOIN models m      ON m.id = g.model_id
              JOIN brands b      ON b.id = m.brand_id
             WHERE oa.oem_number = :oem
             ORDER BY b.name, m.name
             LIMIT 40
        """),
                {"oem": part.oem_number},
            )
        ]

    return templates.TemplateResponse(
        "part.html",
        {
            "request": request,
            "part": dict(part._mapping),
            "photos": photos,
            "fits": fits,
            "condition_label": CONDITION_LABELS.get(part.condition, part.condition),
        },
    )


# ------------------------------------------------------------------
# Поиск по VIN
# ------------------------------------------------------------------


class VinSearch(BaseModel):
    vin: str = Field(min_length=11, max_length=25)


@router.post("/api/catalog/vin")
async def catalog_vin(payload: VinSearch, session: AsyncSession = Depends(get_session)):
    wmi_map = {
        r.code: r.manufacturer
        for r in await session.execute(text("SELECT code, manufacturer FROM wmi"))
    }

    info = decode(payload.vin, wmi_lookup=wmi_map)
    if not info.valid:
        return {"resolution": "invalid", "errors": info.errors}

    result = {
        "vin": info.vin,
        "wmi": info.wmi,
        "vds": info.vds,
        "country": info.country,
        "manufacturer": info.manufacturer,
        "year": info.year,
        "warnings": info.warnings,
    }

    # 1. Точное совпадение по накопленным паттернам
    hit = (
        await session.execute(
            text("SELECT modification_id, confidence FROM match_vin_pattern(:w, :v)"),
            {"w": info.wmi, "v": info.vds},
        )
    ).first()

    if hit:
        chain = (
            await session.execute(
                text("""
            SELECT b.name AS brand, m.name AS model,
                   g.id AS generation_id, g.name AS generation, g.body_type,
                   g.year_from, g.year_to, mo.id AS modification_id
              FROM modifications mo
              LEFT JOIN generations g ON g.id = mo.generation_id
              LEFT JOIN models m      ON m.id = g.model_id
              LEFT JOIN brands b      ON b.id = m.brand_id
             WHERE mo.id = :id
        """),
                {"id": hit.modification_id},
            )
        ).first()

        if chain:
            result.update(resolution="exact", confidence=hit.confidence, **dict(chain._mapping))
            count = await count_fitting(session, chain.generation_id, hit.modification_id)
            result["results_count"] = count
            await log_query(session, info, "exact", chain.generation_id, count)
            return result

    # 2. Знаем завод и год — предлагаем выбрать модель из этой марки
    if info.manufacturer:
        candidates = [
            dict(r._mapping)
            for r in await session.execute(
                text("""
            SELECT g.id AS generation_id, b.name AS brand, m.name AS model,
                   g.name AS generation, g.body_type, g.year_from, g.year_to,
                   (SELECT count(*) FROM parts p
                      LEFT JOIN donors d ON d.id = p.donor_id
                     WHERE d.generation_id = g.id
                       AND p.status = 'in_stock' AND p.published) AS parts_count
              FROM wmi w
              JOIN brands b      ON b.id = w.brand_id
              JOIN models m      ON m.brand_id = b.id
              JOIN generations g ON g.model_id = m.id
             WHERE w.code = :wmi
               AND (CAST(:year AS int) IS NULL OR
                    (g.year_from <= :year AND (g.year_to IS NULL OR g.year_to >= :year)))
             ORDER BY parts_count DESC, m.name
             LIMIT 30
        """),
                {"wmi": info.wmi, "year": info.year},
            )
        ]

        if candidates:
            result.update(resolution="brand_year", candidates=candidates)
            await log_query(
                session,
                info,
                "brand_year",
                None,
                sum(c["parts_count"] for c in candidates),
            )
            return result

    # 3. Ничего не знаем
    result["resolution"] = "unknown"
    await log_query(session, info, "unknown", None, 0)
    return result


async def count_fitting(
    session: AsyncSession, generation_id: int, modification_id: int | None
) -> int:
    return (
        await session.execute(
            text(f"""
        SELECT count(*) FROM parts p LEFT JOIN donors d ON d.id = p.donor_id
         WHERE {FITS_CLAUSE}
    """),
            {"gen": generation_id, "mod": modification_id},
        )
    ).scalar_one()


async def log_query(
    session: AsyncSession, info, resolution: str, generation_id: int | None, count: int
):
    await session.execute(
        text("""
        INSERT INTO vin_queries (vin, wmi, vds, resolution, generation_id, results_count)
        VALUES (:vin, :wmi, :vds, :res, :gen, :cnt)
    """),
        {
            "vin": info.vin,
            "wmi": info.wmi,
            "vds": info.vds,
            "res": resolution,
            "gen": generation_id,
            "cnt": count,
        },
    )
    await session.commit()


# ------------------------------------------------------------------
# Выдача каталога
# ------------------------------------------------------------------

# Деталь подходит машине, если выполнено любое из трёх:
#   1. снята с такой же машины
#   2. проставлена ручная применимость
#   3. её каталожный номер значится применимым к этому поколению
FITS_CLAUSE = """
    p.status = 'in_stock' AND p.published
    AND (
        d.generation_id = :gen
        OR EXISTS (SELECT 1 FROM part_applicability pa
                    WHERE pa.part_id = p.id AND pa.generation_id = :gen)
        OR (p.oem_number IS NOT NULL AND EXISTS (
                SELECT 1 FROM oem_applicability oa
                 WHERE oa.oem_number = p.oem_number
                   AND oa.generation_id = :gen
                   AND (oa.modification_id IS NULL
                        OR CAST(:mod AS int) IS NULL
                        OR oa.modification_id = :mod)))
    )
"""


@router.get("/api/catalog/parts")
async def catalog_parts(
    generation_id: int | None = None,
    modification_id: int | None = None,
    brand_id: int | None = None,
    model_id: int | None = None,
    category_id: int | None = None,
    condition: str | None = Query(None, description="Через запятую: A,B,C"),
    price_min: int | None = None,
    price_max: int | None = None,
    q: str | None = None,
    sort: str = "new",
    page: int = 1,
    session: AsyncSession = Depends(get_session),
):
    where = ["p.status = 'in_stock'", "p.published"]
    params: dict = {"gen": generation_id, "mod": modification_id}

    if generation_id:
        where = [FITS_CLAUSE]
    if brand_id:
        where.append("b.id = :brand")
        params["brand"] = brand_id
    if model_id:
        where.append("m.id = :model")
        params["model"] = model_id
    if category_id:
        # Категория вместе со всеми вложенными
        where.append("""p.category_id IN (
            WITH RECURSIVE sub AS (
                SELECT id FROM part_categories WHERE id = :cat
                UNION ALL
                SELECT c.id FROM part_categories c JOIN sub ON c.parent_id = sub.id
            ) SELECT id FROM sub)""")
        params["cat"] = category_id
    if condition:
        codes = [c for c in condition.upper().split(",") if c in CONDITION_LABELS]
        if codes:
            where.append("p.condition = ANY(CAST(:conds AS part_condition[]))")
            params["conds"] = codes
    if price_min is not None:
        where.append("p.price >= :pmin")
        params["pmin"] = price_min
    if price_max is not None:
        where.append("p.price <= :pmax")
        params["pmax"] = price_max
    if q:
        # Три пути: название, точный номер, кросс через oem_cross.
        # Кросс сужаем по категории — один номер бывает у разных
        # производителей на совершенно разные детали
        where.append("""(
            p.name ILIKE '%' || CAST(:q AS text) || '%'
            OR p.oem_number = CAST(:oem AS text)
            OR EXISTS (
                SELECT 1 FROM oem_cross x1
                  JOIN oem_cross x2 ON x2.art_id = x1.art_id
                  JOIN part_categories pc ON pc.id = p.category_id
                 WHERE x1.code = CAST(:oem AS text)
                   AND x2.code = p.oem_number
                   -- Узел должен совпасть: один номер бывает
                   -- у подшипника и у интеркулера одновременно
                   AND (x2.node IS NULL OR pc.node IS NULL OR x2.node = pc.node)
            )
        )""")
        params["q"] = q.strip()
        params["oem"] = "".join(ch for ch in q.upper() if ch.isalnum())

    order = {
        "new": "p.id DESC",
        "price_asc": "p.price ASC NULLS LAST",
        "price_desc": "p.price DESC NULLS LAST",
    }.get(sort, "p.id DESC")

    params["limit"] = PAGE_SIZE
    params["offset"] = (max(page, 1) - 1) * PAGE_SIZE
    where_sql = " AND ".join(f"({w})" for w in where)

    base_from = """
        FROM parts p
        LEFT JOIN donors d      ON d.id = p.donor_id
        LEFT JOIN generations g ON g.id = d.generation_id
        LEFT JOIN models m      ON m.id = g.model_id
        LEFT JOIN brands b      ON b.id = m.brand_id
        JOIN part_categories c ON c.id = p.category_id
    """

    rows = await session.execute(
        text(f"""
        SELECT p.id, p.sku, p.name, p.condition::text AS condition, p.price,
               p.oem_number, c.name AS category,
               b.name AS brand, m.name AS model, g.name AS generation,
               d.year, d.code AS donor_code,
               (SELECT path FROM part_photos ph
                 WHERE ph.part_id = p.id ORDER BY sort_order LIMIT 1) AS photo,
               (SELECT b2.name || ' ' || m2.name
                  FROM part_applicability pa
                  JOIN generations g2 ON g2.id = pa.generation_id
                  JOIN models m2      ON m2.id = g2.model_id
                  JOIN brands b2      ON b2.id = m2.brand_id
                 WHERE pa.part_id = p.id
                 ORDER BY b2.name LIMIT 1) AS fits_first,
               (SELECT count(*) FROM part_applicability pa
                 WHERE pa.part_id = p.id) AS fits_count
        {base_from}
        WHERE {where_sql}
        ORDER BY {order}
        LIMIT :limit OFFSET :offset
    """),
        params,
    )

    total = (
        await session.execute(text(f"SELECT count(*) {base_from} WHERE {where_sql}"), params)
    ).scalar_one()

    items = []
    for r in rows:
        item = dict(r._mapping)
        item["condition_label"] = CONDITION_LABELS.get(item["condition"])
        items.append(item)

    return {
        "total": total,
        "page": page,
        "pages": -(-total // PAGE_SIZE),
        "items": items,
    }


@router.get("/api/catalog/facets")
async def catalog_facets(
    generation_id: int | None = None,
    modification_id: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Счётчики для боковой панели. Категория с нулём не показывается —
    пустой фильтр раздражает сильнее, чем отсутствие фильтра."""
    params = {"gen": generation_id, "mod": modification_id}
    where = FITS_CLAUSE if generation_id else "p.status = 'in_stock' AND p.published"

    cats = await session.execute(
        text(f"""
        SELECT c.id, c.name, count(*) AS cnt
          FROM parts p
          LEFT JOIN donors d ON d.id = p.donor_id
          JOIN part_categories c ON c.id = p.category_id
         WHERE {where}
         GROUP BY c.id, c.name
         ORDER BY cnt DESC
    """),
        params,
    )

    conds = await session.execute(
        text(f"""
        SELECT p.condition::text AS condition, count(*) AS cnt
          FROM parts p LEFT JOIN donors d ON d.id = p.donor_id
         WHERE {where}
         GROUP BY p.condition ORDER BY p.condition
    """),
        params,
    )

    price = (
        await session.execute(
            text(f"""
        SELECT min(p.price)::int AS min, max(p.price)::int AS max
          FROM parts p LEFT JOIN donors d ON d.id = p.donor_id
         WHERE {where} AND p.price IS NOT NULL
    """),
            params,
        )
    ).first()

    return {
        "categories": [dict(r._mapping) for r in cats],
        "conditions": [
            {**dict(r._mapping), "label": CONDITION_LABELS.get(r.condition)} for r in conds
        ],
        "price": dict(price._mapping) if price else {"min": 0, "max": 0},
    }


# ------------------------------------------------------------------
# Отчёт по спросу
# ------------------------------------------------------------------


@router.get("/api/reports/unmet-demand")
async def unmet_demand(session: AsyncSession = Depends(get_session)):
    """Какие машины искали, но у вас их не оказалось.
    Готовый список на закупку."""
    rows = await session.execute(text("SELECT * FROM unmet_demand LIMIT 50"))
    return [dict(r._mapping) for r in rows]
