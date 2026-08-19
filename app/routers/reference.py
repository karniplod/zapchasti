"""
Справочник: добавление на бегу.

Приёмщик не должен упираться в отсутствующую модель. Если поколения нет —
он заводит его прямо из формы приёмки за десять секунд, машина принимается,
а запись помечается needs_review. Менеджер потом чистит дубли пачкой.

Компромисс осознанный: без этого приёмщик либо ждёт менеджера, либо
записывает машину «примерно на похожее поколение» — а это ломает
применимость в каталоге и найти ошибку потом невозможно.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_role
from ..database import get_session
from ..scripts.import_catalog import slugify

router = APIRouter(prefix="/api/reference", tags=["reference"])

BODY_TYPES = [
    "седан",
    "хэтчбек",
    "универсал",
    "лифтбек",
    "купе",
    "кабриолет",
    "внедорожник",
    "кроссовер",
    "минивэн",
    "пикап",
    "фургон",
]


# ------------------------------------------------------------------
# Схемы
# ------------------------------------------------------------------


class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class ModelIn(BaseModel):
    brand_id: int
    name: str = Field(min_length=1, max_length=60)


class GenerationIn(BaseModel):
    model_id: int
    name: str = Field(min_length=1, max_length=60)
    body_type: str | None = None
    year_from: int
    year_to: int | None = None

    @field_validator("year_from", "year_to")
    @classmethod
    def sane_year(cls, v):
        if v is not None and not (1950 <= v <= 2035):
            raise ValueError("Год вне разумного диапазона")
        return v


class ModificationIn(BaseModel):
    generation_id: int
    engine_code: str | None = None
    engine_volume: float | None = None
    fuel: str | None = None
    power_hp: int | None = None
    transmission: str | None = None
    drive: str | None = None


# ------------------------------------------------------------------
# Создание
# ------------------------------------------------------------------


@router.post("/brands", status_code=201)
async def add_brand(
    payload: BrandIn,
    user=Depends(require_role("dismantler")),
    session: AsyncSession = Depends(get_session),
):
    name = payload.name.strip()
    slug = slugify(name)

    existing = (
        await session.execute(text("SELECT id, name FROM brands WHERE slug = :s"), {"s": slug})
    ).first()
    if existing:
        # Не ошибка: приёмщик набрал «мерседес», а марка есть как
        # «Mercedes-Benz». Отдаём существующую.
        return {"id": existing.id, "name": existing.name, "existed": True}

    row = (
        await session.execute(
            text("""
        INSERT INTO brands (name, slug, source) VALUES (:n, :s, 'manual')
        RETURNING id, name
    """),
            {"n": name, "s": slug},
        )
    ).first()
    await session.commit()
    return {"id": row.id, "name": row.name, "existed": False}


@router.post("/models", status_code=201)
async def add_model(
    payload: ModelIn,
    user=Depends(require_role("dismantler")),
    session: AsyncSession = Depends(get_session),
):
    name = payload.name.strip()
    slug = slugify(name)

    existing = (
        await session.execute(
            text("""
        SELECT id, name FROM models WHERE brand_id = :b AND slug = :s
    """),
            {"b": payload.brand_id, "s": slug},
        )
    ).first()
    if existing:
        return {"id": existing.id, "name": existing.name, "existed": True}

    row = (
        await session.execute(
            text("""
        INSERT INTO models (brand_id, name, slug, source, needs_review)
        VALUES (:b, :n, :s, 'manual', true)
        RETURNING id, name
    """),
            {"b": payload.brand_id, "n": name, "s": slug},
        )
    ).first()
    await session.commit()
    return {"id": row.id, "name": row.name, "existed": False}


@router.post("/generations", status_code=201)
async def add_generation(
    payload: GenerationIn,
    user=Depends(require_role("dismantler")),
    session: AsyncSession = Depends(get_session),
):
    if payload.year_to and payload.year_to < payload.year_from:
        raise HTTPException(422, "Год окончания раньше года начала")

    name = payload.name.strip()

    existing = (
        await session.execute(
            text("""
        SELECT id, name FROM generations WHERE model_id = :m AND lower(name) = lower(:n)
    """),
            {"m": payload.model_id, "n": name},
        )
    ).first()
    if existing:
        return {"id": existing.id, "name": existing.name, "existed": True}

    # Пересечение по годам — почти всегда признак дубля под другим названием.
    # Не блокируем, но предупреждаем: приёмщику решать, он машину видит.
    overlap = (
        await session.execute(
            text("""
        SELECT id, name, year_from, year_to FROM generations
         WHERE model_id = :m
           AND year_from <= COALESCE(:yt, 2035)
           AND COALESCE(year_to, 2035) >= :yf
         LIMIT 1
    """),
            {"m": payload.model_id, "yf": payload.year_from, "yt": payload.year_to},
        )
    ).first()

    row = (
        await session.execute(
            text("""
        INSERT INTO generations (model_id, name, body_type, year_from, year_to,
                                 source, needs_review)
        VALUES (:m, :n, :b, :yf, :yt, 'manual', true)
        RETURNING id, name
    """),
            {
                "m": payload.model_id,
                "n": name,
                "b": payload.body_type,
                "yf": payload.year_from,
                "yt": payload.year_to,
            },
        )
    ).first()
    await session.commit()

    result = {"id": row.id, "name": row.name, "existed": False}
    if overlap:
        result["warning"] = (
            f"Годы пересекаются с поколением «{overlap.name}» "
            f"({overlap.year_from}–{overlap.year_to or 'н.в.'}). "
            f"Проверьте, не то же ли это самое."
        )
    return result


@router.post("/modifications", status_code=201)
async def add_modification(
    payload: ModificationIn,
    user=Depends(require_role("dismantler")),
    session: AsyncSession = Depends(get_session),
):
    row = (
        await session.execute(
            text("""
        INSERT INTO modifications (generation_id, engine_code, engine_volume,
                                   fuel, power_hp, transmission, drive)
        VALUES (:g, :ec, :ev, :f, :p, :t, :d)
        RETURNING id
    """),
            {
                "g": payload.generation_id,
                "ec": payload.engine_code,
                "ev": payload.engine_volume,
                "f": payload.fuel,
                "p": payload.power_hp,
                "t": payload.transmission,
                "d": payload.drive,
            },
        )
    ).first()
    await session.commit()
    return {"id": row.id}


@router.get("/body-types")
async def body_types():
    return BODY_TYPES


# ------------------------------------------------------------------
# Проверка добавленного
# ------------------------------------------------------------------


@router.get("/review")
async def review_queue(
    user=Depends(require_role("manager")), session: AsyncSession = Depends(get_session)
):
    """Что завели руками и никто не проверил. Отсортировано по числу машин:
    ошибка в поколении с десятью донорами дороже, чем с одним."""
    rows = await session.execute(text("SELECT * FROM reference_review LIMIT 100"))
    return [dict(r._mapping) for r in rows]


@router.post("/generations/{generation_id}/approve", status_code=204)
async def approve(
    generation_id: int,
    user=Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    await session.execute(
        text("""
        UPDATE generations SET needs_review = false WHERE id = :id
    """),
        {"id": generation_id},
    )
    await session.commit()


@router.post("/generations/{generation_id}/merge", status_code=200)
async def merge(
    generation_id: int,
    into_id: int,
    user=Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    """Слить дубль в правильное поколение: переносим доноров и применимость,
    затем удаляем лишнюю запись."""
    if generation_id == into_id:
        raise HTTPException(422, "Нельзя слить поколение само в себя")

    moved = (
        await session.execute(
            text("""
        UPDATE donors SET generation_id = :into WHERE generation_id = :from_id
        RETURNING id
    """),
            {"into": into_id, "from_id": generation_id},
        )
    ).rowcount

    await session.execute(
        text("""
        UPDATE modifications SET generation_id = :into WHERE generation_id = :from_id
    """),
        {"into": into_id, "from_id": generation_id},
    )

    # Применимость может задублироваться — переносим только уникальное
    await session.execute(
        text("""
        INSERT INTO oem_applicability (oem_number, generation_id, modification_id)
        SELECT oem_number, :into, modification_id
          FROM oem_applicability WHERE generation_id = :from_id
        ON CONFLICT DO NOTHING
    """),
        {"into": into_id, "from_id": generation_id},
    )

    await session.execute(
        text("""
        DELETE FROM oem_applicability WHERE generation_id = :from_id
    """),
        {"from_id": generation_id},
    )

    await session.execute(
        text("""
        INSERT INTO part_applicability (part_id, generation_id)
        SELECT part_id, :into FROM part_applicability WHERE generation_id = :from_id
        ON CONFLICT DO NOTHING
    """),
        {"into": into_id, "from_id": generation_id},
    )

    await session.execute(
        text("""
        DELETE FROM generations WHERE id = :from_id
    """),
        {"from_id": generation_id},
    )

    await session.commit()
    return {"merged": True, "donors_moved": moved}
