"""
Форма приёмки автомобиля на разбор.

Поток:
  1. Приёмщик вводит VIN -> /api/vin/decode
  2. Декодер отдаёт страну, завод, год + подставляет модификацию,
     если такой VDS уже встречался
  3. Приёмщик проверяет/исправляет цепочку марка -> модель -> поколение -> модификация
  4. Сохранение -> донор создан, паттерн VIN запомнен

Перед запуском нужна последовательность для внутренних номеров:
  CREATE SEQUENCE donor_code_seq START 1;
"""

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, require_role
from ..database import get_session  # ваш штатный провайдер сессии
from ..templating import templates
from ..vin_decoder import decode, normalize

router = APIRouter(tags=["intake"])

MEDIA_ROOT = Path("media/donors")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_PHOTO_BYTES = 12 * 1024 * 1024


# ------------------------------------------------------------------
# Схемы
# ------------------------------------------------------------------


class VinRequest(BaseModel):
    vin: str = Field(min_length=11, max_length=25)


class DonorCreate(BaseModel):
    vin: str | None = None
    generation_id: int
    modification_id: int | None = None
    year: int | None = None
    color: str | None = None
    mileage_km: int | None = None
    plate: str | None = None
    purchase_price: float | None = None
    notes: str | None = None

    @field_validator("vin")
    @classmethod
    def clean_vin(cls, v):
        if not v:
            return None
        v = normalize(v)
        return v if len(v) == 17 else None

    @field_validator("year")
    @classmethod
    def sane_year(cls, v):
        if v is not None and not (1950 <= v <= 2030):
            raise ValueError("Год вне допустимого диапазона")
        return v


# ------------------------------------------------------------------
# Страница
# ------------------------------------------------------------------


@router.get("/intake", response_class=HTMLResponse)
async def intake_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse(
        "admin/intake.html", {"request": request, "user": user}
    )


# ------------------------------------------------------------------
# Декодирование VIN
# ------------------------------------------------------------------


async def load_wmi(session: AsyncSession) -> dict:
    rows = await session.execute(text("SELECT code, manufacturer FROM wmi"))
    return {r.code: r.manufacturer for r in rows}


@router.post("/api/vin/decode")
async def decode_vin(payload: VinRequest, session: AsyncSession = Depends(get_session)):
    wmi_map = await load_wmi(session)

    # Совпадение по накопленным паттернам делает сама БД
    async def pattern_lookup(wmi: str, vds: str):
        row = (
            await session.execute(
                text("SELECT modification_id, confidence FROM match_vin_pattern(:w, :v)"),
                {"w": wmi, "v": vds},
            )
        ).first()
        return (row.modification_id, row.confidence) if row else None

    info = decode(payload.vin, wmi_lookup=wmi_map)
    if not info.valid:
        return {"valid": False, "errors": info.errors, "vin": info.vin}

    hit = await pattern_lookup(info.wmi, info.vds)
    chain = None
    if hit:
        info.modification_id, info.pattern_confidence = hit
        chain = await modification_chain(session, hit[0])
    else:
        info.warnings.append("Модификация неизвестна — выберите вручную, паттерн запомнится")

    # Такой VIN уже принимали? Показать сразу, до заполнения формы.
    duplicate = (
        await session.execute(
            text("SELECT code, status FROM donors WHERE vin = :vin"), {"vin": info.vin}
        )
    ).first()

    return {
        "valid": True,
        "vin": info.vin,
        "wmi": info.wmi,
        "vds": info.vds,
        "serial": info.serial,
        "country": info.country,
        "manufacturer": info.manufacturer,
        "year": info.year,
        "year_candidates": info.year_candidates,
        "plant_code": info.plant_code,
        "modification_id": info.modification_id,
        "confidence": info.pattern_confidence,
        "chain": chain,
        "warnings": info.warnings,
        "duplicate": {"code": duplicate.code, "status": duplicate.status} if duplicate else None,
    }


async def modification_chain(session: AsyncSession, modification_id: int):
    """Развернуть модификацию в цепочку марка -> модель -> поколение, чтобы
    форма сразу выставила все четыре селекта."""
    row = (
        await session.execute(
            text("""
        SELECT b.id AS brand_id, b.name AS brand,
               m.id AS model_id,  m.name AS model,
               g.id AS generation_id, g.name AS generation,
               g.year_from, g.year_to,
               mo.id AS modification_id,
               concat_ws(' ', mo.engine_volume, mo.fuel, mo.transmission, mo.drive) AS modification
          FROM modifications mo
          JOIN generations g ON g.id = mo.generation_id
          JOIN models m      ON m.id = g.model_id
          JOIN brands b      ON b.id = m.brand_id
         WHERE mo.id = :id
    """),
            {"id": modification_id},
        )
    ).first()
    return dict(row._mapping) if row else None


# ------------------------------------------------------------------
# Каскадные справочники
# ------------------------------------------------------------------


@router.get("/api/brands")
async def brands(session: AsyncSession = Depends(get_session)):
    rows = await session.execute(text("SELECT id, name FROM brands ORDER BY name"))
    return [dict(r._mapping) for r in rows]


@router.get("/api/models")
async def models(brand_id: int, session: AsyncSession = Depends(get_session)):
    rows = await session.execute(
        text("SELECT id, name FROM models WHERE brand_id = :b ORDER BY name"),
        {"b": brand_id},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/api/generations")
async def generations(model_id: int, session: AsyncSession = Depends(get_session)):
    rows = await session.execute(
        text("""
        SELECT id, name, body_type, year_from, year_to
          FROM generations WHERE model_id = :m
         ORDER BY year_from DESC
    """),
        {"m": model_id},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/api/modifications")
async def modifications(generation_id: int, session: AsyncSession = Depends(get_session)):
    rows = await session.execute(
        text("""
        SELECT id, engine_code, engine_volume, fuel, power_hp, transmission, drive
          FROM modifications WHERE generation_id = :g
         ORDER BY engine_volume, power_hp
    """),
        {"g": generation_id},
    )
    return [dict(r._mapping) for r in rows]


# ------------------------------------------------------------------
# Создание донора
# ------------------------------------------------------------------


@router.post("/api/donors", status_code=201)
async def create_donor(
    payload: DonorCreate,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("manager")),
):
    if payload.vin:
        dup = (
            await session.execute(
                text("SELECT code FROM donors WHERE vin = :v"), {"v": payload.vin}
            )
        ).first()
        if dup:
            raise HTTPException(409, f"VIN уже принят под номером {dup.code}")

    code = (
        await session.execute(
            text("SELECT 'D-' || lpad(nextval('donor_code_seq')::text, 4, '0') AS code")
        )
    ).scalar_one()

    donor_id = (
        await session.execute(
            text("""
        INSERT INTO donors (code, vin, generation_id, modification_id, year, color,
                            mileage_km, plate, purchase_price, notes, vin_source)
        VALUES (:code, :vin, :gen, :mod, :year, :color,
                :mileage, :plate, :price, :notes, :src)
        RETURNING id
    """),
            {
                "code": code,
                "vin": payload.vin,
                "gen": payload.generation_id,
                "mod": payload.modification_id,
                "year": payload.year,
                "color": payload.color,
                "mileage": payload.mileage_km,
                "plate": payload.plate,
                "price": payload.purchase_price,
                "notes": payload.notes,
                "src": "manual" if payload.vin else "no_vin",
            },
        )
    ).scalar_one()

    # Приёмщик подтвердил модификацию — запоминаем паттерн.
    # Следующая такая же машина определится сама.
    if payload.vin and payload.modification_id:
        await session.execute(
            text("SELECT learn_vin_pattern(:w, :v, :m, NULL)"),
            {
                "w": payload.vin[0:3],
                "v": payload.vin[3:8],
                "m": payload.modification_id,
            },
        )

    await session.commit()
    return {"id": donor_id, "code": code}


# ------------------------------------------------------------------
# Фото осмотра
# ------------------------------------------------------------------


@router.post("/api/donors/{donor_id}/photos", status_code=201)
async def upload_photos(
    donor_id: int,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("manager")),
):
    exists = (
        await session.execute(text("SELECT code FROM donors WHERE id = :id"), {"id": donor_id})
    ).first()
    if not exists:
        raise HTTPException(404, "Донор не найден")

    folder = MEDIA_ROOT / str(donor_id)
    folder.mkdir(parents=True, exist_ok=True)
    saved = []

    for order, upload in enumerate(files):
        if upload.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(415, f"{upload.filename}: только JPEG, PNG или WebP")

        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}[
            upload.content_type
        ]
        name = f"{uuid.uuid4().hex}{ext}"
        target = folder / name

        # Имя файла из браузера в путь не попадает — только сгенерированное
        with target.open("wb") as out:
            shutil.copyfileobj(upload.file, out, length=1024 * 1024)

        if target.stat().st_size > MAX_PHOTO_BYTES:
            target.unlink()
            raise HTTPException(413, f"{upload.filename}: больше 12 МБ")

        rel = f"/media/donors/{donor_id}/{name}"
        await session.execute(
            text("""
            INSERT INTO donor_photos (donor_id, path, sort_order)
            VALUES (:d, :p, :o)
        """),
            {"d": donor_id, "p": rel, "o": order},
        )
        saved.append(rel)

    await session.commit()
    return {"photos": saved}
