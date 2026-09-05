"""Приём запчастей вне рабочего места разборщика.

Два случая, и они по-разному считают применимость:

1. Деталь с уже принятой машины — выбирается донор, применимость и
   артикул берутся от него, как при разборе. Нужно, когда деталь сняли
   не в общем потоке разбора: вернули с примерки, нашли в кузове позже,
   разобрали машину частично.

2. Деталь, приехавшая отдельно (выкуплена, под заказ, новая). Донора
   нет, поэтому применимость обязательна вручную — иначе деталь не
   попадёт ни в один фильтр каталога.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_role
from ..database import get_session
from ..templating import templates

router = APIRouter(tags=["stock"])

SOURCES = {
    "donor": "С принятой машины",
    "purchased": "Куплена б/у",
    "new": "Новая",
}
CONDITIONS = {"A", "B", "C", "D"}


@router.get("/stock/new", response_class=HTMLResponse)
async def stock_page(request: Request, user=Depends(require_role("manager"))):
    return templates.TemplateResponse(
        "admin/stock_new.html", {"request": request, "user": user, "sources": SOURCES}
    )


@router.get("/api/stock/donors")
async def acceptable_donors(session: AsyncSession = Depends(get_session)):
    """Машины, с которых ещё можно снимать детали. Утилизированные
    и полностью разобранные не предлагаем."""
    rows = await session.execute(
        text("""
        SELECT d.id, d.code, b.name AS brand, m.name AS model,
               g.name AS generation, d.year
          FROM donors d
          JOIN generations g ON g.id = d.generation_id
          JOIN models m ON m.id = g.model_id
          JOIN brands b ON b.id = m.brand_id
         WHERE d.status IN ('accepted', 'dismantling')
         ORDER BY d.id DESC
    """)
    )
    return [dict(r._mapping) for r in rows]


@router.post("/api/stock/parts", status_code=201)
async def create_standalone(
    category_id: int = Form(...),
    name: str = Form(...),
    condition: str = Form(...),
    source: str = Form(...),
    # Донор указан — применимость берётся от него, generations не нужен
    donor_id: int | None = Form(None),
    generations: str = Form(""),  # id поколений через запятую
    oem_number: str | None = Form(None),
    condition_note: str | None = Form(None),
    price: Decimal | None = Form(None),
    location: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    user=Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    if condition not in CONDITIONS:
        raise HTTPException(422, "Состояние должно быть A, B, C или D")
    if source not in SOURCES:
        raise HTTPException(422, "Неизвестный источник поступления")

    gen_ids = [int(g) for g in generations.split(",") if g.strip().isdigit()]

    if donor_id is not None:
        # Деталь с принятой машины: применимость и артикул — от неё,
        # руками указывать нечего
        row = (
            await session.execute(
                text("""
            UPDATE donors SET part_counter = part_counter + 1
             WHERE id = :id AND status IN ('accepted', 'dismantling')
            RETURNING code, part_counter, generation_id
        """),
                {"id": donor_id},
            )
        ).first()
        if not row:
            raise HTTPException(404, "Машина не найдена или с неё уже нельзя снимать детали")
        sku = f"{row.code}-{row.part_counter:04d}"
        gen_ids = [row.generation_id]
    else:
        if not gen_ids:
            raise HTTPException(
                422, "Укажите машину или хотя бы одну модель, к которой подходит деталь"
            )
        sku = (
            await session.execute(
                text("SELECT 'P-' || lpad(nextval('standalone_part_seq')::text, 4, '0')")
            )
        ).scalar_one()

    oem = "".join(c for c in (oem_number or "").upper() if c.isalnum()) or None

    part_id = (
        await session.execute(
            text("""
        INSERT INTO parts (sku, donor_id, category_id, name, oem_number, condition,
                           condition_note, price, location, status, published, source)
        VALUES (:sku, :donor, :cat, :name, :oem, CAST(:cond AS part_condition),
                :note, :price, :loc, CAST(:st AS part_status), :pub, :src)
        RETURNING id
    """),
            {
                "sku": sku,
                "donor": donor_id,
                "cat": category_id,
                "name": name.strip(),
                "oem": oem,
                "cond": condition,
                "note": condition_note,
                "price": price,
                "loc": location,
                "st": "in_stock" if files else "draft",
                "pub": bool(files and price),
                "src": source,
            },
        )
    ).scalar_one()

    # Ручная применимость — единственный способ найти такую деталь
    for gid in gen_ids:
        await session.execute(
            text("""
            INSERT INTO part_applicability (part_id, generation_id)
            VALUES (:p, :g) ON CONFLICT DO NOTHING
        """),
            {"p": part_id, "g": gid},
        )

    saved = []
    if files:
        from .dismantle import MEDIA_ROOT, save_upload

        folder = MEDIA_ROOT / str(part_id)
        for order, up in enumerate(files):
            fname = save_upload(up, folder)
            rel = f"/media/parts/{part_id}/{fname}"
            await session.execute(
                text("""
                INSERT INTO part_photos (part_id, path, sort_order)
                VALUES (:p, :path, :o)
            """),
                {"p": part_id, "path": rel, "o": order},
            )
            saved.append(rel)

    await session.commit()
    return {"id": part_id, "sku": sku, "generations": len(gen_ids), "photos": saved}


@router.get("/api/stock/parts")
async def standalone_list(
    session: AsyncSession = Depends(get_session), user=Depends(require_role("manager"))
):
    rows = await session.execute(
        text("""
        SELECT p.id, p.sku, p.name, p.condition::text, p.price, p.status::text,
               p.source, c.name AS category,
               (SELECT count(*) FROM part_applicability pa WHERE pa.part_id = p.id) AS fits
          FROM parts p JOIN part_categories c ON c.id = p.category_id
         WHERE p.donor_id IS NULL
         ORDER BY p.id DESC LIMIT 100
    """)
    )
    return [dict(r._mapping) for r in rows]
