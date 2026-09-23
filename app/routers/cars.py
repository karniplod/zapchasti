"""Машины на витрине: список и карточка машины.

Покупатель ищет не только деталь, но и машину: «есть у вас разобранная
Веста?» Здесь все машины — ждут разбора, в разборе и разобранные, у каждой
что уже снято и форма вопроса «а снимете ли под заказ».

Покупателю показываем то, что помогает подобрать деталь: поколение,
двигатель, коробку, пробег, цвет, где стоит машина и описание, которое
приёмщик пишет специально для сайта (donors.public_note). Не показываем
госномер, цену закупки и внутренние заметки приёмщика. VIN — только
первые 11 знаков: по ним видны завод, модель и год, но не конкретная
машина.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import optional_user
from ..database import get_session
from ..templating import templates

router = APIRouter(tags=["cars"])

# Статусы, которые видит покупатель. «Принята» тоже: разбирать ещё
# не начали, все детали на машине — для «снять под заказ» лучше не бывает.
# «Утилизирована» — снимать уже нечего
PUBLIC = ("accepted", "dismantling", "dismantled")
STATUS_LABELS = {"accepted": "Ждёт разбора", "dismantling": "В разборе",
                 "dismantled": "Разобрана"}

CARS_SQL = """
    SELECT d.id, d.code, d.status::text AS status, d.year, d.mileage_km, d.color,
           d.accepted_at, d.public_note,
           b.id AS brand_id, b.name AS brand, m.id AS model_id, m.name AS model,
           g.name AS generation, g.body_type,
           mo.engine_volume, mo.fuel, mo.power_hp, mo.transmission, mo.drive,
           br.city,
           (SELECT count(*) FROM parts p
             WHERE p.donor_id = d.id AND p.status = 'in_stock' AND p.published) AS parts,
           (SELECT coalesce(ph.thumb, ph.path) FROM donor_photos ph
             WHERE ph.donor_id = d.id ORDER BY ph.sort_order LIMIT 1) AS photo
      FROM donors d
      JOIN generations g ON g.id = d.generation_id
      JOIN models m      ON m.id = g.model_id
      JOIN brands b      ON b.id = m.brand_id
      LEFT JOIN modifications mo ON mo.id = d.modification_id
      LEFT JOIN branches br      ON br.id = d.branch_id
     WHERE d.status IN ('accepted', 'dismantling', 'dismantled')
"""

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


# Сколько марок показывать плашками. Остальные уходят в список: на
# складе с сотнями марок стена плашек занимала два с половиной экрана,
# и до самих машин приходилось листать
CHIPS = 14


def top_brands(brands, chosen: int | None) -> list[dict]:
    """Частые марки плашками. Выбранная — всегда среди них, иначе
    непонятно, по чему отфильтровано."""
    top = sorted(brands, key=lambda b: (-b["cnt"], b["name"].lower()))[:CHIPS]
    if chosen and not any(b["id"] == chosen for b in top):
        rest = [b for b in brands if b["id"] == chosen]
        top = top[:CHIPS - 1] + rest
    return sorted(top, key=lambda b: b["name"].lower())


def plural(n: int, one: str, few: str, many: str) -> str:
    a, b = n % 10, n % 100
    if a == 1 and b != 11:
        return one
    if 2 <= a <= 4 and not 12 <= b <= 14:
        return few
    return many


def engine_line(c) -> str:
    """«1.6 л, бензин, 106 л.с.» — чем короче, тем легче сравнить машины."""
    bits = []
    if c.engine_volume:
        bits.append(f"{c.engine_volume:g} л")
    if c.fuel:
        bits.append(c.fuel.lower())
    if c.power_hp:
        bits.append(f"{c.power_hp} л.с.")
    return ", ".join(bits)


def km(n: int | None) -> str | None:
    return f"{n:,} км".replace(",", " ") if n else None


def since(d: date | None) -> str | None:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}" if d else None


def card(c) -> dict:
    """Строка из базы плюс готовые подписи — шаблону не нужно считать."""
    return {
        **dict(c._mapping),
        "engine": engine_line(c),
        "mileage": km(c.mileage_km),
        "parts_label": f"{c.parts} {plural(c.parts, 'деталь', 'детали', 'деталей')}",
        "status_label": STATUS_LABELS.get(c.status, c.status),
        # С машины снимают под заказ, пока разбор не закрыт
        "open": c.status in ("accepted", "dismantling"),
    }


def order(c: dict):
    # Сначала те, с которых можно снять под заказ (ждут разбора и
    # в разборе), потом разобранные. Внутри — свежие выше
    return (not c["open"], -(c["accepted_at"] or date.min).toordinal(), -c["id"])


# Сколько машин показывать сразу и сколько добавлять по кнопке.
# Раньше страница отдавала все: на складе с четырьмя сотнями машин это
# 421 карточка в одном списке и мегабайт разметки
PAGE = 20
MAX_SHOWN = 400   # предел на случай ?n=99999 в адресе

# Порядок тот же, что в order(): сначала те, с которых можно снять под
# заказ, внутри — свежие выше. Считается теперь базой, а не Питоном,
# иначе для сортировки пришлось бы снова тянуть все строки
ORDER_SQL = " ORDER BY (d.status = 'dismantled'), d.accepted_at DESC, d.id DESC"
# CAST, а не :brand::int — двоеточие сразу после имени параметра
# драйвер принимает за начало следующего параметра
FILTER_SQL = (" AND (CAST(:brand AS int) IS NULL OR b.id = CAST(:brand AS int))"
              " AND (CAST(:model AS int) IS NULL OR m.id = CAST(:model AS int))")

COUNT_SQL = """
    SELECT count(*)
      FROM donors d
      JOIN generations g ON g.id = d.generation_id
      JOIN models m      ON m.id = g.model_id
      JOIN brands b      ON b.id = m.brand_id
     WHERE d.status IN ('accepted', 'dismantling', 'dismantled')
"""

BRANDS_SQL = """
    SELECT b.id, b.name, count(*)::int AS cnt
      FROM donors d
      JOIN generations g ON g.id = d.generation_id
      JOIN models m      ON m.id = g.model_id
      JOIN brands b      ON b.id = m.brand_id
     WHERE d.status IN ('accepted', 'dismantling', 'dismantled')
     GROUP BY b.id, b.name
"""

MODELS_SQL = """
    SELECT m.id, m.name, count(*)::int AS cnt
      FROM donors d
      JOIN generations g ON g.id = d.generation_id
      JOIN models m      ON m.id = g.model_id
     WHERE d.status IN ('accepted', 'dismantling', 'dismantled')
       AND m.brand_id = :brand
     GROUP BY m.id, m.name
"""


async def page_of_cars(session, brand, model, offset: int, limit: int) -> list[dict]:
    """Одна страница выдачи — ровно limit строк из базы."""
    rows = await session.execute(
        text(CARS_SQL + FILTER_SQL + ORDER_SQL + " LIMIT :limit OFFSET :offset"),
        {"brand": brand, "model": model, "limit": limit, "offset": offset})
    return [card(r) for r in rows]


@router.get("/cars", response_class=HTMLResponse)
async def cars_page(
    request: Request,
    brand: int | None = None,
    model: int | None = None,
    n: int = PAGE,
    session: AsyncSession = Depends(get_session),
):
    user = await optional_user(request, session)
    # n — сколько карточек на странице. Без скрипта «показать ещё» —
    # обычная ссылка, которая увеличивает n; со скриптом дозагрузка идёт
    # без перезагрузки страницы (static/js/cars.js)
    n = max(PAGE, min(n, MAX_SHOWN))

    found = (await session.execute(text(COUNT_SQL + FILTER_SQL),
                                   {"brand": brand, "model": model})).scalar()
    total = (await session.execute(text(COUNT_SQL),
                                   {"brand": None, "model": None})).scalar()
    cars = await page_of_cars(session, brand, model, 0, n)

    brands = [dict(r._mapping) for r in await session.execute(text(BRANDS_SQL))]
    models = ([dict(r._mapping) for r in
               await session.execute(text(MODELS_SQL), {"brand": brand})] if brand else [])

    return templates.TemplateResponse(
        "cars.html",
        {
            "request": request,
            "user": user,
            "cars": cars,
            "total": total,
            "found": found,
            "shown": len(cars),
            "page": PAGE,
            "count_label": f"{found} {plural(found, 'машина', 'машины', 'машин')}",
            "brands": sorted(brands, key=lambda b: b["name"].lower()),
            "brands_top": top_brands(brands, brand),
            "models": sorted(models, key=lambda m: m["name"].lower()),
            "brand": brand,
            "model": model,
        },
    )


@router.get("/cars/more", response_class=HTMLResponse)
async def cars_more(
    request: Request,
    offset: int,
    brand: int | None = None,
    model: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Следующие 20 карточек — для кнопки «показать ещё». Отдаёт только
    плитки, без страницы: скрипт вставляет их в конец списка."""
    offset = max(0, min(offset, MAX_SHOWN))
    cars = await page_of_cars(session, brand, model, offset, PAGE)
    return templates.TemplateResponse("_car_rows.html",
                                      {"request": request, "cars": cars})


@router.get("/cars/{code}", response_class=HTMLResponse)
async def car_page(code: str, request: Request, session: AsyncSession = Depends(get_session)):
    user = await optional_user(request, session)
    row = (
        await session.execute(text(CARS_SQL + " AND d.code = :code"),
                              {"code": code.strip().upper()})
    ).first()
    if not row:
        raise HTTPException(404, "Машина не найдена или уже не на витрине")
    car = card(row)

    extra = (
        await session.execute(
            text("""
        SELECT d.vin, cp.name AS complectation, br.name AS branch,
               br.address AS branch_address, br.phone AS branch_phone
          FROM donors d
          LEFT JOIN complectations cp ON cp.id = d.complectation_id
          LEFT JOIN branches br       ON br.id = d.branch_id
         WHERE d.id = :id
    """),
            {"id": car["id"]},
        )
    ).first()
    car.update(
        complectation=extra.complectation,
        branch=extra.branch,
        branch_address=extra.branch_address,
        branch_phone=extra.branch_phone,
        # Первые 11 знаков: завод, модель, год, завод сборки — хватает,
        # чтобы сверить комплектацию. Серийный номер оставляем при себе
        vin_head=extra.vin[:11] if extra.vin else None,
        since=since(car["accepted_at"]),
    )

    photos = [r.path for r in await session.execute(
        text("SELECT path FROM donor_photos WHERE donor_id = :id ORDER BY sort_order"),
        {"id": car["id"]},
    )]

    # Снятые детали — те же поля, что у плитки на главной
    parts = [dict(r._mapping) for r in await session.execute(
        text("""
        SELECT p.sku, p.name, p.condition::text AS condition, p.price,
               parent.name AS node,
               (SELECT coalesce(ph.thumb, ph.path) FROM part_photos ph
                 WHERE ph.part_id = p.id ORDER BY ph.sort_order LIMIT 1) AS photo
          FROM parts p
          JOIN part_categories c ON c.id = p.category_id
          LEFT JOIN part_categories parent ON parent.id = c.parent_id
         WHERE p.donor_id = :id AND p.status = 'in_stock' AND p.published
         ORDER BY parent.name, p.name
    """),
        {"id": car["id"]},
    )]

    return templates.TemplateResponse(
        "car.html",
        {"request": request, "user": user, "car": car, "photos": photos, "parts": parts},
    )
