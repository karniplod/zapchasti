"""
Разбор донора на детали.

Рабочее место разборщика: телефон или планшет в цеху, грязные руки,
20-40 деталей с одной машины. Поэтому:
  - деталь создаётся ОДНИМ запросом вместе с фото (меньше обрывов на плохом wifi)
  - артикул присваивается атомарно счётчиком донора
  - деталь без фото сохраняется как черновик и не попадает в каталог

Перед запуском:
  ALTER TABLE donors ADD COLUMN part_counter int NOT NULL DEFAULT 0;
  pip install segno
"""

import shutil
import uuid
from decimal import Decimal
from pathlib import Path

import segno
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_role
from ..config import settings
from ..database import get_session
from ..services import oem as oem_service
from ..templating import templates

router = APIRouter(tags=["dismantle"])

MEDIA_ROOT = Path("media/parts")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
EXT_BY_TYPE = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_PHOTO_BYTES = 12 * 1024 * 1024

# Тип детали и состояние — правила в app/validation/catalog.py
from ..validation import require  # noqa: E402
from ..validation.catalog import check_condition, check_origin  # noqa: E402


# ------------------------------------------------------------------
# Страница рабочего места
# ------------------------------------------------------------------


@router.get("/donors/{donor_id}/dismantle", response_class=HTMLResponse)
async def dismantle_page(
    donor_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    donor = await fetch_donor(session, donor_id)
    if not donor:
        raise HTTPException(404, "Донор не найден")
    # У закрытой машины та же страница, но без формы: список снятого
    # и этикетки нужны и после разбора
    return templates.TemplateResponse(
        "admin/dismantle.html",
        {"request": request, "donor": donor, "open": donor["status"] in OPEN_STATUSES},
    )


async def fetch_donor(session: AsyncSession, donor_id: int):
    row = (
        await session.execute(
            text("""
        SELECT d.id, d.code, d.vin, d.year, d.color, d.status::text AS status,
               b.name AS brand, m.name AS model, g.name AS generation,
               g.body_type,
               (SELECT count(*) FROM parts p WHERE p.donor_id = d.id) AS parts_count
          FROM donors d
          JOIN generations g ON g.id = d.generation_id
          JOIN models m      ON m.id = g.model_id
          JOIN brands b      ON b.id = m.brand_id
         WHERE d.id = :id
    """),
            {"id": donor_id},
        )
    ).first()
    return dict(row._mapping) if row else None


# Снимать детали можно, пока машина принята или в разборе. Разобранную
# возвращают в разбор явно — если что-то забыли снять; утилизированную
# нет: кузов сдан, снимать с него нечего
OPEN_STATUSES = {"accepted", "dismantling"}


async def donor_status(session: AsyncSession, donor_id: int) -> str | None:
    return (
        await session.execute(
            text("SELECT status::text FROM donors WHERE id = :id"), {"id": donor_id}
        )
    ).scalar()


def closed_or_missing(status: str | None) -> HTTPException:
    """Почему к машине нельзя добавить деталь — словами для разборщика."""
    if status is None:
        return HTTPException(404, "Донор не найден")
    if status == "scrapped":
        return HTTPException(409, "Машина утилизирована — снимать с неё нечего")
    return HTTPException(409, "Разбор закрыт — верните машину в разбор")


@router.get("/api/donors/{donor_id}")
async def donor_info(
    donor_id: int,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    donor = await fetch_donor(session, donor_id)
    if not donor:
        raise HTTPException(404, "Донор не найден")
    return donor


# ------------------------------------------------------------------
# Категории
# ------------------------------------------------------------------


@router.get("/api/part-categories")
async def part_categories(
    q: str | None = None,
    # Весь список разом — приложение держит его на телефоне и ищет по нему,
    # когда в цеху пропал wifi: без категории деталь не сохранить
    all: bool = False,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    """Плоский список конечных категорий с полным путём.
    Разборщику нужен поиск, а не раскрывающееся дерево — быстрее набрать
    «дверь пер» чем кликать три уровня."""
    rows = await session.execute(
        text("""
        WITH RECURSIVE tree AS (
            SELECT id, parent_id, name, name::text AS path, 1 AS depth
              FROM part_categories WHERE parent_id IS NULL
            UNION ALL
            SELECT c.id, c.parent_id, c.name, t.path || ' / ' || c.name, t.depth + 1
              FROM part_categories c JOIN tree t ON c.parent_id = t.id
        )
        SELECT t.id, t.name, t.path
          FROM tree t
          JOIN part_categories pc ON pc.id = t.id
         WHERE NOT EXISTS (SELECT 1 FROM part_categories c WHERE c.parent_id = t.id)
           -- Ветки, оставленные под будущее наполнение, детей не имеют
           -- и без этого попадали бы в подбор как обычные категории
           AND NOT pc.is_placeholder
           AND (CAST(:q AS text) IS NULL OR t.path ILIKE '%' || CAST(:q AS text) || '%')
         ORDER BY t.path
         LIMIT CASE WHEN CAST(:all AS boolean) THEN NULL ELSE 60 END
    """),
        {"q": q, "all": all},
    )
    return [dict(r._mapping) for r in rows]


# ------------------------------------------------------------------
# Создание детали
# ------------------------------------------------------------------


def save_upload(upload: UploadFile, folder: Path) -> str:
    if upload.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(415, f"{upload.filename}: только JPEG, PNG или WebP")
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{EXT_BY_TYPE[upload.content_type]}"
    target = folder / name
    with target.open("wb") as out:
        shutil.copyfileobj(upload.file, out, length=1024 * 1024)
    if target.stat().st_size > MAX_PHOTO_BYTES:
        target.unlink()
        raise HTTPException(413, f"{upload.filename}: больше 12 МБ")
    return name


@router.get("/api/oem/suggest")
async def oem_suggest(
    category_id: int,
    donor_id: int | None = None,
    generation_id: int | None = None,
    modification_id: int | None = None,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    """Кандидаты в каталожный номер для этой машины и этого узла.

    Машину можно задать донором — так зовёт рабочее место разборщика,
    ему известен только id машины.
    """
    if donor_id and not (generation_id and modification_id):
        row = (
            await session.execute(
                text("""
            SELECT d.generation_id, d.modification_id, b.name AS brand
              FROM donors d
              JOIN generations g ON g.id = d.generation_id
              JOIN models m      ON m.id = g.model_id
              JOIN brands b      ON b.id = m.brand_id
             WHERE d.id = :id
        """),
                {"id": donor_id},
            )
        ).first()
        if row:
            generation_id = generation_id or row.generation_id
            modification_id = modification_id or row.modification_id

    result = await oem_service.suggest(
        session,
        category_id=category_id,
        generation_id=generation_id,
        modification_id=modification_id,
    )
    return result


@router.get("/api/oem/accuracy")
async def oem_accuracy(
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("manager")),
):
    """Попадание источников подсказки. Это и есть ответ на вопрос,
    нужен ли платный каталог: будет цифра, а не ощущение."""
    return await oem_service.accuracy(session)


async def part_by_client_key(session: AsyncSession, key: uuid.UUID) -> dict | None:
    """Деталь, уже созданная по этому ключу, — в том же виде, что ответ
    create_part. None — такой ещё не было."""
    row = (
        await session.execute(
            text("""
        SELECT id, sku, status::text AS status, oem_number FROM parts
         WHERE client_key = :k
    """),
            {"k": key},
        )
    ).first()
    if not row:
        return None
    photos = [
        r.path
        for r in await session.execute(
            text("SELECT path FROM part_photos WHERE part_id = :p ORDER BY sort_order"),
            {"p": row.id},
        )
    ]
    applicability = 0
    if row.oem_number:
        applicability = (
            await session.execute(
                text("SELECT count(*) FROM oem_applicability WHERE oem_number = :oem"),
                {"oem": row.oem_number},
            )
        ).scalar_one()
    return {
        "id": row.id,
        "sku": row.sku,
        "status": row.status,
        "photos": photos,
        "applicability_rows": applicability,
    }


@router.post("/api/parts", status_code=201)
async def create_part(
    donor_id: int = Form(...),
    category_id: int = Form(...),
    name: str = Form(..., min_length=2, max_length=200),
    condition: str = Form(...),
    oem_number: str | None = Form(None, max_length=40),
    # Какую подсказку нажал разборщик; пусто — набрал руками
    oem_source: str | None = Form(None),
    # Оригинал / ОЕМ / аналог: от этого зависит, чей номер искать
    origin: str = Form("original"),
    part_brand: str | None = Form(None, max_length=80),
    condition_note: str | None = Form(None, max_length=500),
    price: Decimal | None = Form(None, ge=0, le=100_000_000),
    location: str | None = Form(None, max_length=40),
    weight_kg: Decimal | None = Form(None, ge=0, le=5000),
    # Сколько одинаковых штук: четыре диска с машины — одна деталь «4 шт».
    # Приложение сотрудника поле не шлёт — тогда одна
    quantity: int = Form(1, ge=1, le=9999),
    # Размер для доставки: S / M / L / XL (app/delivery.py)
    size_class: str | None = Form(None, pattern="^(S|M|L|XL)$"),
    # Ключ, который придумал телефон. Связь в цеху рвётся: запрос дошёл,
    # ответ потерялся — приложение шлёт деталь ещё раз. С тем же ключом
    # сервер отдаёт уже созданную, а не заводит вторую с новым артикулом
    client_key: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    require(check_condition(condition))
    require(check_origin(origin))

    key = None
    if client_key:
        try:
            key = uuid.UUID(client_key)
        except ValueError:
            raise HTTPException(422, "Неверный ключ детали") from None
        if done := await part_by_client_key(session, key):
            return done

    # Атомарный счётчик деталей донора: UPDATE ... RETURNING держит блокировку
    # строки, поэтому два разборщика на одной машине не получат один артикул.
    row = (
        await session.execute(
            text("""
        UPDATE donors
           SET part_counter = part_counter + 1,
               -- Снятая деталь и означает, что машину начали разбирать.
               -- Без этого перехода она стояла в «принята» до самого
               -- закрытия разбора и не попадала на витрину: каталог
               -- показывает машины со статусом dismantling/dismantled
               status = CASE WHEN status = 'accepted'
                             THEN 'dismantling'::donor_status
                             ELSE status END
         WHERE id = :id
           -- Проверка статуса здесь, а не отдельным SELECT до UPDATE:
           -- между ними машину успели бы закрыть с соседнего телефона
           AND status IN ('accepted', 'dismantling')
        RETURNING code, part_counter, generation_id, modification_id
    """),
            {"id": donor_id},
        )
    ).first()
    if not row:
        raise closed_or_missing(await donor_status(session, donor_id))

    sku = f"{row.code}-{row.part_counter:04d}"

    # Нормализация каталожного номера: в базе он должен быть без пробелов,
    # дефисов и точек, иначе применимость не найдётся
    oem = oem_service.normalize(oem_number) or None

    # Без фото — черновик. Каталог такие не показывает.
    status = "in_stock" if files else "draft"

    try:
        part_id = (
            await session.execute(
                text("""
            INSERT INTO parts (sku, donor_id, category_id, name, oem_number, condition,
                               condition_note, price, location, weight_kg, status, published,
                               oem_source, oem_verified, origin, part_brand,
                               branch_id, client_key, quantity, size_class)
            VALUES (:sku, :donor, :cat, :name, :oem, CAST(:cond AS part_condition),
                    :note, :price, :loc, :weight, CAST(:status AS part_status), :pub,
                    -- Откуда номер и сверен ли он с деталью — решает код ниже
                    :oem_source, :oem_verified, :origin, :part_brand,
                    -- Деталь появляется там же, где стоит машина. Дальше её
                    -- можно перевезти, и филиал детали разойдётся с машиной
                    (SELECT branch_id FROM donors WHERE id = :donor), :key, :qty, :size)
            RETURNING id
        """),
                {
                    "qty": quantity, "size": size_class,
                    "sku": sku,
                    "donor": donor_id,
                    "cat": category_id,
                    "name": name.strip(),
                    "oem": oem,
                    "cond": condition,
                    "note": condition_note,
                    "price": price,
                    "loc": location,
                    "weight": weight_kg,
                    "status": status,
                    "pub": bool(files and price),
                    "oem_source": (oem_source or "manual") if oem else None,
                    # Сверенным считается только номер, набранный руками с детали.
                    # Принятый из подсказки — эхо источника, а не новое
                    # подтверждение: иначе номер из поиска, сохранённый один раз,
                    # вернулся бы «своей историей» и сам себя подтвердил
                    "oem_verified": bool(oem) and not oem_source,
                    "origin": origin,
                    # У оригинала бренд — это марка машины, отдельно не храним
                    "part_brand": (part_brand or "").strip() or None,
                    "key": key,
                },
            )
        ).scalar_one()
    except IntegrityError:
        # Тот же ключ пришёл вторым запросом, пока первый ещё сохранялся
        # (телефон не дождался ответа и повторил). Первый уже закоммитил —
        # отдаём его деталь; счётчик артикулов откатится вместе с этим
        await session.rollback()
        if key and (done := await part_by_client_key(session, key)):
            return done
        raise

    # Что предлагали источники и что выбрал человек — разметка, по которой
    # считается точность каждого источника
    await oem_service.record(
        session,
        part_id,
        oem,
        category_id=category_id,
        generation_id=row.generation_id,
        modification_id=row.modification_id,
    )

    folder = MEDIA_ROOT / str(part_id)
    saved = []
    for order, upload in enumerate(files):
        fname = save_upload(upload, folder)
        rel = f"/media/parts/{part_id}/{fname}"
        await session.execute(
            text("""
            INSERT INTO part_photos (part_id, path, sort_order) VALUES (:p, :path, :o)
        """),
            {"p": part_id, "path": rel, "o": order},
        )
        saved.append(rel)

    # Применимость подтягивается по OEM-номеру, если он уже известен системе
    applicability = 0
    if oem:
        applicability = (
            await session.execute(
                text("""
            SELECT count(*) FROM oem_applicability WHERE oem_number = :oem
        """),
                {"oem": oem},
            )
        ).scalar_one()

    await session.commit()
    return {
        "id": part_id,
        "sku": sku,
        "status": status,
        "photos": saved,
        "applicability_rows": applicability,
    }


@router.get("/api/donors/{donor_id}/parts")
async def donor_parts(
    donor_id: int,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    rows = await session.execute(
        text("""
        SELECT p.id, p.sku, p.name, p.condition::text, p.price, p.status::text, p.quantity,
               p.location, c.name AS category,
               (SELECT path FROM part_photos ph
                 WHERE ph.part_id = p.id ORDER BY sort_order LIMIT 1) AS photo
          FROM parts p JOIN part_categories c ON c.id = p.category_id
         WHERE p.donor_id = :d
         ORDER BY p.id DESC
    """),
        {"d": donor_id},
    )
    return [dict(r._mapping) for r in rows]


@router.delete("/api/parts/{part_id}", status_code=204)
async def delete_part(
    part_id: int,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    """Удалять можно только то, что ещё не продано."""
    row = (
        await session.execute(
            text("SELECT status::text AS status FROM parts WHERE id = :id"),
            {"id": part_id},
        )
    ).first()
    if not row:
        raise HTTPException(404, "Деталь не найдена")
    if row.status == "sold":
        raise HTTPException(409, "Проданную деталь нельзя удалить — спишите её")

    await session.execute(text("DELETE FROM parts WHERE id = :id"), {"id": part_id})
    shutil.rmtree(MEDIA_ROOT / str(part_id), ignore_errors=True)
    await session.commit()


# ------------------------------------------------------------------
# Этикетки с QR
# ------------------------------------------------------------------


def qr_svg(data: str, size: int = 3) -> str:
    """Инлайн-SVG: не требует Pillow и печатается чётко на любом принтере."""
    return segno.make(data, error="m").svg_inline(scale=size, border=0)


@router.get("/donors/{donor_id}/labels", response_class=HTMLResponse)
async def print_labels(
    donor_id: int,
    request: Request,
    only_new: bool = True,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    """Страница для печати. only_new=True — только детали без напечатанной
    этикетки, чтобы не переводить лист заново после добавления пяти штук."""
    donor = await fetch_donor(session, donor_id)
    if not donor:
        raise HTTPException(404, "Донор не найден")

    rows = await session.execute(
        text("""
        SELECT p.id, p.sku, p.name, p.condition::text AS condition,
               p.location, c.name AS category
          FROM parts p JOIN part_categories c ON c.id = p.category_id
         WHERE p.donor_id = :d
           AND (CAST(:all AS boolean) OR p.label_printed_at IS NULL)
         ORDER BY p.id
    """),
        {"d": donor_id, "all": not only_new},
    )

    car = f"{donor['brand']} {donor['model']}" + (f" {donor['year']}" if donor["year"] else "")
    labels = [label_of(r, car) for r in rows]

    return templates.TemplateResponse(
        "admin/labels.html",
        {
            "request": request,
            "title": f"Этикетки {donor['code']}",
            "heading": donor["code"],
            "subheading": f"{donor['brand']} {donor['model']}",
            "labels": labels,
            "reprint_url": "?only_new=false",
        },
    )


def label_of(r, car: str) -> dict:
    """Данные одной этикетки. Домен из настроек, как в sitemap: этикетка
    живёт на детали годами, и QR с заглушкой вместо адреса уже не
    перепечатать незаметно."""
    url = f"{settings.base_url}/p/{r.sku}"
    return {**dict(r._mapping), "qr": qr_svg(url), "url": url, "car": car}


@router.get("/parts/{part_id}/label", response_class=HTMLResponse)
async def print_part_label(
    part_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    """Этикетка одной детали — из списка деталей и со страницы разбора.
    Работает и для детали без машины (куплена б/у, новая): вместо машины
    на этикетке первая модель из применимости."""
    r = (await session.execute(text("""
        SELECT p.id, p.sku, p.name, p.condition::text AS condition,
               p.location, c.name AS category,
               b.name AS d_brand, m.name AS d_model, d.year AS d_year,
               fit.label AS fit, fit.cnt AS fit_cnt
          FROM parts p
          JOIN part_categories c ON c.id = p.category_id
          LEFT JOIN donors d      ON d.id = p.donor_id
          LEFT JOIN generations g ON g.id = d.generation_id
          LEFT JOIN models m      ON m.id = g.model_id
          LEFT JOIN brands b      ON b.id = m.brand_id
          LEFT JOIN LATERAL (
                SELECT min(b2.name || ' ' || m2.name) AS label, count(*) AS cnt
                  FROM part_applicability pa
                  JOIN generations g2 ON g2.id = pa.generation_id
                  JOIN models m2      ON m2.id = g2.model_id
                  JOIN brands b2      ON b2.id = m2.brand_id
                 WHERE pa.part_id = p.id) fit ON true
         WHERE p.id = :id"""), {"id": part_id})).first()
    if not r:
        raise HTTPException(404, "Деталь не найдена")

    if r.d_brand:
        car = f"{r.d_brand} {r.d_model}" + (f" {r.d_year}" if r.d_year else "")
    elif r.fit:
        car = r.fit + (f" и ещё {r.fit_cnt - 1}" if r.fit_cnt > 1 else "")
    else:
        car = "без машины"

    return templates.TemplateResponse(
        "admin/labels.html",
        {
            "request": request,
            "title": f"Этикетка {r.sku}",
            "heading": r.sku,
            "subheading": r.name,
            "labels": [label_of(r, car)],
            "reprint_url": None,
        },
    )


@router.post("/api/parts/labels/printed", status_code=204)
async def mark_parts_printed(
    payload: dict,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    """Отметка «этикетка напечатана» для любых деталей — и с машины, и
    принятых отдельно: у тех нет машины, значит, и адреса с её номером."""
    ids = [int(i) for i in (payload.get("ids") or [])]
    if not ids:
        return
    await session.execute(
        text("UPDATE parts SET label_printed_at = now() WHERE id = ANY(:ids)"),
        {"ids": ids})
    await session.commit()


@router.post("/api/donors/{donor_id}/labels/printed", status_code=204)
async def mark_printed(
    donor_id: int,
    payload: dict,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    """Вызывается после window.print(). Требует:
    ALTER TABLE parts ADD COLUMN label_printed_at timestamptz;"""
    ids = payload.get("ids") or []
    if not ids:
        return
    await session.execute(
        text("""
        UPDATE parts SET label_printed_at = now()
         WHERE donor_id = :d AND id = ANY(:ids)
    """),
        {"d": donor_id, "ids": ids},
    )
    await session.commit()


# ------------------------------------------------------------------
# Завершение разбора
# ------------------------------------------------------------------


@router.post("/api/donors/{donor_id}/finish")
async def finish_donor(
    donor_id: int,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    """Закрыть разбор. Черновики без фото придётся дофотографировать —
    иначе они навсегда останутся невидимыми в каталоге."""
    drafts = (
        await session.execute(
            text("""
        SELECT count(*) FROM parts WHERE donor_id = :d AND status = 'draft'
    """),
            {"d": donor_id},
        )
    ).scalar_one()

    if drafts:
        raise HTTPException(409, f"Осталось черновиков без фото: {drafts}")

    # Закрыть можно только открытый разбор: повторное нажатие на
    # разобранной машине — не ошибка данных, но и не действие; а утилизированную
    # «закрытие» вернуло бы из утилизированных в разобранные
    closed = await session.execute(
        text("""
        UPDATE donors SET status = 'dismantled'
         WHERE id = :d AND status IN ('accepted', 'dismantling')
        RETURNING id
    """),
        {"d": donor_id},
    )
    if not closed.first():
        status = await donor_status(session, donor_id)
        if status is None:
            raise HTTPException(404, "Донор не найден")
        raise HTTPException(409, "Машина утилизирована" if status == "scrapped"
                            else "Разбор уже закрыт")
    await session.commit()
    return {"status": "dismantled"}


@router.post("/api/donors/{donor_id}/reopen")
async def reopen_donor(
    donor_id: int,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_role("dismantler")),
):
    """Вернуть разобранную машину в разбор — если что-то забыли снять.

    Только из «разобрана»: утилизированную вернуть нельзя, кузов уже сдан.
    Отдельное действие, а не просто открытая форма: так видно, что
    машину открыли заново, и закрыть её потом нужно тоже явно.
    """
    reopened = await session.execute(
        text("""
        UPDATE donors SET status = 'dismantling'
         WHERE id = :d AND status = 'dismantled'
        RETURNING id
    """),
        {"d": donor_id},
    )
    if not reopened.first():
        status = await donor_status(session, donor_id)
        if status is None:
            raise HTTPException(404, "Донор не найден")
        raise HTTPException(409, "Машина утилизирована — вернуть в разбор нельзя"
                            if status == "scrapped" else "Разбор и так открыт")
    await session.commit()
    return {"status": "dismantling"}
