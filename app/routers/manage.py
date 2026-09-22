"""Управление складом: список машин и таблица деталей.

Всё, что создано в приёмке и разборе, правится отсюда: цена, состояние,
место, публикация. Без этого экрана любая опечатка остаётся навсегда.
"""

import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, require_role
from ..config import settings
from ..database import get_session
from ..services import oem as oem_service
from ..services.images import save_images
from ..templating import templates
from ..vin_decoder import normalize
from .dismantle import ORIGINS

router = APIRouter(tags=["manage"])

STATUSES = ["draft", "in_stock", "reserved", "sold", "written_off"]


@router.get("/donors", response_class=HTMLResponse)
async def donors_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse("admin/donors.html", {"request": request, "user": user})


@router.get("/parts", response_class=HTMLResponse)
async def parts_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse("admin/parts.html", {"request": request, "user": user})


@router.get("/api/branches")
async def branches(session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    """Для выпадающих списков в приёмке и правке."""
    rows = await session.execute(
        text("""
        SELECT id, city, name, city || ', ' || name AS label
          FROM branches WHERE is_active
         ORDER BY sort_order, city, name
    """)
    )
    return [dict(r._mapping) for r in rows]


@router.get("/api/manage/donors")
async def donors_list(
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    rows = await session.execute(
        text("""
        SELECT d.id, d.code, d.vin, d.year, d.color, d.status::text AS status,
               d.accepted_at, d.purchase_price, d.mileage_km, d.plate, d.notes,
               d.public_note,
               d.modification_id, d.generation_id, d.branch_id,
               (SELECT br.city || ', ' || br.name FROM branches br
                 WHERE br.id = d.branch_id) AS branch,
               b.name AS brand, m.name AS model, g.name AS generation,
               (SELECT coalesce(ph.thumb, ph.path) FROM donor_photos ph
                 WHERE ph.donor_id = d.id
                 ORDER BY ph.sort_order LIMIT 1)                       AS photo,
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
    # Со сводки приходят по конкретной проблеме: без цены, скрытые,
    # без номера, с несверенным номером
    issue: str | None = None,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    rows = await session.execute(
        text("""
        SELECT p.id, p.sku, p.name, p.condition::text AS condition, p.price,
               p.status::text AS status, p.location, p.published, p.oem_number,
               p.condition_note, p.weight_kg, p.category_id, p.branch_id,
               p.origin, p.part_brand, p.oem_verified,
               (SELECT br.city || ', ' || br.name FROM branches br
                 WHERE br.id = p.branch_id) AS branch,
               p.source, c.name AS category,
               (SELECT pc.name FROM part_categories pc
                 WHERE pc.id = c.parent_id) AS node,
               d.code AS donor_code,
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
           AND (CAST(:issue AS text) IS NULL OR CASE CAST(:issue AS text)
                    WHEN 'no_price'   THEN p.status = 'in_stock' AND p.price IS NULL
                    WHEN 'hidden'     THEN p.status = 'in_stock' AND NOT p.published
                    WHEN 'no_number'  THEN p.status = 'in_stock' AND p.oem_number IS NULL
                    WHEN 'unverified' THEN p.status = 'in_stock'
                                       AND p.oem_number IS NOT NULL AND NOT p.oem_verified
                    ELSE true END)
         ORDER BY p.id DESC LIMIT 300
    """),
        {"q": q, "st": status, "d": donor_id, "pr": problems, "issue": issue},
    )
    return [dict(r._mapping) for r in rows]


class PartPatch(BaseModel):
    price: Decimal | None = None
    condition: str | None = None
    location: str | None = None
    status: str | None = None
    published: bool | None = None
    # Поля полного редактора: в списке они не показываются, правятся
    # по кнопке — их меняют редко, а место занимают на каждой строке
    name: str | None = None
    category_id: int | None = None
    oem_number: str | None = None
    condition_note: str | None = None
    weight_kg: Decimal | None = None
    # Деталь можно перевезти в другой филиал независимо от машины
    branch_id: int | None = None
    # Оригинал / ОЕМ / аналог и бренд детали для двух последних
    origin: str | None = None
    part_brand: str | None = None


class DonorPatch(BaseModel):
    """Правка карточки машины. Поколение здесь не меняется: на нём висит
    применимость уже снятых деталей, и смена молча увела бы их не к той
    машине. Для этого есть слияние поколений в справочнике."""

    vin: str | None = None
    year: int | None = None
    color: str | None = None
    mileage_km: int | None = None
    plate: str | None = None
    purchase_price: Decimal | None = None
    accepted_at: date | None = None
    notes: str | None = None
    # Пустая строка — стереть описание; None — не трогать
    public_note: str | None = Field(default=None, max_length=2000)
    status: str | None = None
    modification_id: int | None = None
    complectation_id: int | None = None
    branch_id: int | None = None


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

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(422, "Название не может быть пустым")
        sets.append("name = :name")
        params["name"] = name

    if payload.category_id is not None:
        # Ветки дерева, оставленные под будущее наполнение, деталью
        # занимать нельзя — их же прячет и подбор категории
        cat = (
            await session.execute(
                text("""
            SELECT is_placeholder,
                   EXISTS (SELECT 1 FROM part_categories c
                            WHERE c.parent_id = pc.id) AS has_children
              FROM part_categories pc WHERE pc.id = :c
        """),
                {"c": payload.category_id},
            )
        ).first()
        if not cat:
            raise HTTPException(422, "Категория не найдена")
        if cat.is_placeholder or cat.has_children:
            raise HTTPException(422, "Выберите конечную категорию, а не раздел")
        sets.append("category_id = :cat")
        params["cat"] = payload.category_id

    if payload.oem_number is not None:
        # Тот же разбор, что и при создании: номера сверяют по буквам
        # и цифрам, разделители у каждого каталога свои
        oem = oem_service.normalize(payload.oem_number) or None
        sets.append("oem_number = :oem")
        params["oem"] = oem
        # Номер правил человек, глядя на деталь, — это и есть проверка
        sets.append("oem_verified = :oemv")
        params["oemv"] = bool(oem)
        sets.append("oem_source = :oems")
        params["oems"] = "manual" if oem else None

    if payload.origin is not None:
        if payload.origin not in ORIGINS:
            raise HTTPException(422, "Тип детали: оригинал, ОЕМ или аналог")
        sets.append("origin = :origin")
        params["origin"] = payload.origin
        # У оригинала своего бренда нет — он равен марке машины
        if payload.origin == "original":
            sets.append("part_brand = NULL")

    if payload.part_brand is not None and payload.origin != "original":
        sets.append("part_brand = :pbrand")
        params["pbrand"] = payload.part_brand.strip() or None

    if payload.condition_note is not None:
        sets.append("condition_note = :note")
        params["note"] = payload.condition_note.strip() or None

    if payload.weight_kg is not None:
        if payload.weight_kg < 0:
            raise HTTPException(422, "Вес не может быть отрицательным")
        sets.append("weight_kg = :weight")
        params["weight"] = payload.weight_kg

    if payload.branch_id is not None:
        sets.append("branch_id = :branch")
        params["branch"] = payload.branch_id

    if not sets:
        raise HTTPException(422, "Нечего менять")

    sets.append("updated_at = now()")

    await session.execute(text(f"UPDATE parts SET {', '.join(sets)} WHERE id = :id"), params)
    await session.commit()
    return {"ok": True}


DONOR_STATUSES = {"accepted", "dismantling", "dismantled", "scrapped"}


@router.patch("/api/manage/donors/{donor_id}")
async def patch_donor(
    donor_id: int,
    payload: DonorPatch,
    user=Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    cur = (
        await session.execute(
            text("SELECT id, generation_id FROM donors WHERE id = :id"), {"id": donor_id}
        )
    ).first()
    if not cur:
        raise HTTPException(404, "Машина не найдена")

    sets, params = [], {"id": donor_id}

    if payload.vin is not None:
        vin = normalize(payload.vin) or None
        if vin:
            if len(vin) != 17:
                raise HTTPException(422, "VIN должен быть из 17 символов")
            dup = (
                await session.execute(
                    text("SELECT code FROM donors WHERE vin = :v AND id <> :id"),
                    {"v": vin, "id": donor_id},
                )
            ).first()
            if dup:
                raise HTTPException(409, f"Этот VIN уже стоит у машины {dup.code}")
        sets.append("vin = :vin")
        params["vin"] = vin

    if payload.status is not None:
        if payload.status not in DONOR_STATUSES:
            raise HTTPException(422, "Неизвестный статус машины")
        sets.append("status = CAST(:st AS donor_status)")
        params["st"] = payload.status

    if payload.modification_id is not None:
        # Модификация обязана принадлежать тому же поколению, иначе
        # в карточке окажется двигатель от другой машины
        ok = (
            await session.execute(
                text("SELECT 1 FROM modifications WHERE id = :m AND generation_id = :g"),
                {"m": payload.modification_id, "g": cur.generation_id},
            )
        ).first()
        if not ok:
            raise HTTPException(422, "Модификация не из этого поколения")
        sets.append("modification_id = :mod")
        params["mod"] = payload.modification_id

    if payload.complectation_id is not None:
        sets.append("complectation_id = :compl")
        params["compl"] = payload.complectation_id

    if payload.branch_id is not None:
        sets.append("branch_id = :branch")
        params["branch"] = payload.branch_id

    if payload.year is not None:
        if not (1950 <= payload.year <= 2030):
            raise HTTPException(422, "Год вне допустимого диапазона")
        sets.append("year = :year")
        params["year"] = payload.year

    if payload.accepted_at is not None:
        if payload.accepted_at.year < 2000 or payload.accepted_at > date.today():
            raise HTTPException(422, "Дата приёмки вне допустимого диапазона")
        sets.append("accepted_at = :acc")
        params["acc"] = payload.accepted_at

    for field, column in (
        ("color", "color"),
        ("plate", "plate"),
        ("notes", "notes"),
        ("public_note", "public_note"),
        ("mileage_km", "mileage_km"),
        ("purchase_price", "purchase_price"),
    ):
        value = getattr(payload, field)
        if value is not None:
            # Пустая строка означает «очистить поле», а не текст из пробелов
            if isinstance(value, str):
                value = value.strip() or None
            sets.append(f"{column} = :{field}")
            params[field] = value

    if not sets:
        raise HTTPException(422, "Нечего сохранять")

    await session.execute(
        text(f"UPDATE donors SET {', '.join(sets)} WHERE id = :id"), params
    )
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
    shutil.rmtree(settings.media_root / "parts" / str(part_id), ignore_errors=True)


# ------------------------------------------------------------------
# Фото деталей и машин
# ------------------------------------------------------------------

@router.get("/api/manage/parts/{part_id}/photos")
async def part_photos(
    part_id: int, user=Depends(current_user), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        text("""
        SELECT id, path, coalesce(thumb, path) AS thumb, sort_order
          FROM part_photos WHERE part_id = :p ORDER BY sort_order, id
    """),
        {"p": part_id},
    )
    return [dict(r._mapping) for r in rows]


@router.post("/api/manage/parts/{part_id}/photos", status_code=201)
async def add_part_photos(
    part_id: int,
    files: list[UploadFile] = File(...),
    user=Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    exists = (
        await session.execute(text("SELECT 1 FROM parts WHERE id = :id"), {"id": part_id})
    ).first()
    if not exists:
        raise HTTPException(404, "Деталь не найдена")

    start = (
        await session.execute(
            text("SELECT coalesce(max(sort_order), -1) + 1 FROM part_photos WHERE part_id = :p"),
            {"p": part_id},
        )
    ).scalar_one()

    images = await save_images(files, settings.media_root / "parts" / str(part_id))
    saved = []
    for i, img in enumerate(images):
        await session.execute(
            text("""
            INSERT INTO part_photos (part_id, path, thumb, width, height, sort_order)
            VALUES (:p, :path, :thumb, :w, :h, :o)
        """),
            {
                "p": part_id,
                "path": img.path,
                "thumb": img.thumb,
                "w": img.width,
                "h": img.height,
                "o": start + i,
            },
        )
        saved.append(img.path)

    # Появилось фото — деталь больше не черновик
    await session.execute(
        text("""
        UPDATE parts SET status = 'in_stock'
         WHERE id = :id AND status = 'draft'
    """),
        {"id": part_id},
    )

    await session.commit()
    return {"photos": saved}


@router.delete("/api/manage/photos/{photo_id}", status_code=204)
async def delete_photo(
    photo_id: int,
    user=Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    """Удаляем запись, потом файл: если запись не удалилась,
    снимок останется на месте, а не потеряется."""
    row = (
        await session.execute(
            text("""
        SELECT part_id, path FROM part_photos WHERE id = :id
    """),
            {"id": photo_id},
        )
    ).first()
    if not row:
        raise HTTPException(404, "Фото не найдено")

    await session.execute(text("DELETE FROM part_photos WHERE id = :id"), {"id": photo_id})

    left = (
        await session.execute(
            text("SELECT count(*) FROM part_photos WHERE part_id = :p"), {"p": row.part_id}
        )
    ).scalar_one()

    # Без фото деталь нельзя показывать покупателю
    if left == 0:
        await session.execute(
            text("""
            UPDATE parts SET published = false WHERE id = :p
        """),
            {"p": row.part_id},
        )

    await session.commit()

    # path хранится как /media/parts/42/имя.webp — отрезаем префикс
    for suffix in ("", "_t"):
        rel = row.path.removeprefix("/media/")
        f = settings.media_root / Path(rel).with_stem(Path(rel).stem + suffix)
        f.unlink(missing_ok=True)


@router.get("/api/manage/donors/{donor_id}/photos")
async def donor_photos(
    donor_id: int, user=Depends(current_user), session: AsyncSession = Depends(get_session)
):
    rows = await session.execute(
        text("""
        SELECT id, path, coalesce(thumb, path) AS thumb, sort_order
          FROM donor_photos WHERE donor_id = :d ORDER BY sort_order, id
    """),
        {"d": donor_id},
    )
    return [dict(r._mapping) for r in rows]


@router.post("/api/manage/donors/{donor_id}/photos", status_code=201)
async def add_donor_photos(
    donor_id: int,
    files: list[UploadFile] = File(...),
    user=Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    start = (
        await session.execute(
            text("SELECT coalesce(max(sort_order), -1) + 1 FROM donor_photos WHERE donor_id = :d"),
            {"d": donor_id},
        )
    ).scalar_one()

    images = await save_images(files, settings.media_root / "donors" / str(donor_id))
    for i, img in enumerate(images):
        await session.execute(
            text("""
            INSERT INTO donor_photos (donor_id, path, thumb, width, height, sort_order)
            VALUES (:d, :path, :thumb, :w, :h, :o)
        """),
            {
                "d": donor_id,
                "path": img.path,
                "thumb": img.thumb,
                "w": img.width,
                "h": img.height,
                "o": start + i,
            },
        )

    await session.commit()
    return {"count": len(images)}


@router.delete("/api/manage/donor-photos/{photo_id}", status_code=204)
async def delete_donor_photo(
    photo_id: int,
    user=Depends(require_role("manager")),
    session: AsyncSession = Depends(get_session),
):
    row = (
        await session.execute(
            text("SELECT path FROM donor_photos WHERE id = :id"), {"id": photo_id}
        )
    ).first()
    if not row:
        raise HTTPException(404, "Фото не найдено")

    await session.execute(text("DELETE FROM donor_photos WHERE id = :id"), {"id": photo_id})
    await session.commit()

    # path хранится как /media/parts/42/имя.webp — отрезаем префикс
    for suffix in ("", "_t"):
        rel = row.path.removeprefix("/media/")
        f = settings.media_root / Path(rel).with_stem(Path(rel).stem + suffix)
        f.unlink(missing_ok=True)


# ------------------------------------------------------------------
# Заказы с витрины
# ------------------------------------------------------------------


@router.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, user=Depends(current_user)):
    return templates.TemplateResponse("admin/orders.html", {"request": request, "user": user})


@router.get("/api/manage/orders")
async def orders_list(
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    rows = await session.execute(
        text("""
        SELECT o.id, o.number, o.status::text AS status, o.total, o.created_at,
               o.paid_at, o.delivery_method, o.delivery_address, o.comment,
               c.phone, c.name AS customer_name,
               (SELECT count(*) FROM order_items oi WHERE oi.order_id = o.id) AS items
          FROM orders o
          LEFT JOIN customers c ON c.id = o.customer_id
         WHERE (CAST(:st AS text) IS NULL OR o.status::text = CAST(:st AS text))
         ORDER BY o.created_at DESC
         LIMIT 200
    """),
        {"st": status},
    )
    orders = [dict(r._mapping) for r in rows]
    if not orders:
        return []

    items = await session.execute(
        text("""
        SELECT oi.order_id, oi.price, p.sku, p.name, p.status::text AS status,
               (SELECT br.city || ', ' || br.name FROM branches br
                 WHERE br.id = p.branch_id) AS branch
          FROM order_items oi
          JOIN parts p ON p.id = oi.part_id
         WHERE oi.order_id = ANY(:ids)
         ORDER BY oi.id
    """),
        {"ids": [o["id"] for o in orders]},
    )
    by_order: dict[int, list] = {}
    for r in items:
        by_order.setdefault(r.order_id, []).append(dict(r._mapping))
    for o in orders:
        o["items"] = by_order.get(o["id"], [])
    return orders


class OrderPatch(BaseModel):
    status: str


# Куда можно перевести заказ. Список, а не свободный переход: «отменён»
# возвращает детали на витрину, и случайный клик из «выдан» в «новый»
# выложил бы проданное обратно
ORDER_FLOW = {"new", "confirmed", "paid", "shipped", "completed", "cancelled"}


@router.patch("/api/manage/orders/{order_id}")
async def patch_order(
    order_id: int,
    payload: OrderPatch,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("manager")),
):
    """Статус заказа ведёт менеджер: онлайн-оплаты нет, оплату он
    отмечает руками после того, как деньги получены."""
    if payload.status not in ORDER_FLOW:
        raise HTTPException(422, "Неизвестный статус")

    order = (
        await session.execute(
            text("SELECT id, status::text AS status FROM orders WHERE id = :id"),
            {"id": order_id},
        )
    ).first()
    if not order:
        raise HTTPException(404, "Заказ не найден")

    await session.execute(
        text("""
        UPDATE orders
           SET status = CAST(:st AS order_status),
               paid_at = CASE WHEN :st IN ('paid', 'shipped', 'completed')
                              THEN coalesce(paid_at, now()) ELSE paid_at END
         WHERE id = :id
    """),
        {"st": payload.status, "id": order_id},
    )

    parts = [
        r.part_id
        for r in await session.execute(
            text("SELECT part_id FROM order_items WHERE order_id = :id"), {"id": order_id}
        )
    ]

    if payload.status == "cancelled":
        # Деталь возвращается на витрину — но только та, что лежит
        # в резерве под этот заказ, а не уже проданная кому-то ещё
        await session.execute(
            text("""
            UPDATE parts SET status = 'in_stock', updated_at = now()
             WHERE id = ANY(:ids) AND status = 'reserved'
        """),
            {"ids": parts},
        )
    elif payload.status in ("paid", "shipped", "completed"):
        await session.execute(
            text("""
            UPDATE parts SET status = 'sold', updated_at = now()
             WHERE id = ANY(:ids) AND status IN ('reserved', 'in_stock')
        """),
            {"ids": parts},
        )

    await session.commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Заявки с витрины
# ------------------------------------------------------------------
# Заявки собирались, но прочитать их было негде: плитка в сводке вела
# на несуществующий адрес. Живут на странице заказов вкладкой — работа
# одна и та же: человек оставил обращение, на него надо ответить.


@router.get("/api/manage/leads")
async def leads_list(
    processed: bool | None = None,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    rows = await session.execute(
        text("""
        SELECT l.id, l.phone, l.name, l.message, l.processed, l.created_at,
               p.sku, p.name AS part_name, p.status::text AS part_status,
               d.code AS donor_code, d.status::text AS donor_status,
               concat_ws(' ', b.name, m.name, d.year) AS donor_car
          FROM leads l
          LEFT JOIN parts p ON p.id = l.part_id
          LEFT JOIN donors d      ON d.id = l.donor_id
          LEFT JOIN generations g ON g.id = d.generation_id
          LEFT JOIN models m      ON m.id = g.model_id
          LEFT JOIN brands b      ON b.id = m.brand_id
         WHERE (CAST(:pr AS boolean) IS NULL OR l.processed = CAST(:pr AS boolean))
         ORDER BY l.processed, l.created_at DESC
         LIMIT 200
    """),
        {"pr": processed},
    )
    return [dict(r._mapping) for r in rows]


class LeadPatch(BaseModel):
    processed: bool


@router.patch("/api/manage/leads/{lead_id}")
async def patch_lead(
    lead_id: int,
    payload: LeadPatch,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("manager")),
):
    """Отметка «обработана». Заявку не удаляем: по ней видно, о чём
    спрашивают и чего не хватает на складе."""
    row = (
        await session.execute(
            text("UPDATE leads SET processed = :p WHERE id = :id RETURNING id"),
            {"p": payload.processed, "id": lead_id},
        )
    ).first()
    if not row:
        raise HTTPException(404, "Заявка не найдена")
    await session.commit()
    return {"ok": True}
