"""Тестовые данные: пять машин с VIN, по пять снятых деталей.

    python -m app.scripts.seed_test_data                    покажет, что будет создано
    python -m app.scripts.seed_test_data --apply            создаст
    python -m app.scripts.seed_test_data --remove           покажет, что будет удалено
    python -m app.scripts.seed_test_data --remove --apply   уберёт всё, что создал

Машины подобраны под слабые места декодера VIN:
  • Vesta седан и Vesta универсал — VDS начинается одинаково (GF),
    на них видно совпадение по семейству, а не по модификации;
  • ВАЗ-2107 1995 года — код года S значит и 1995, и 2025;
  • Solaris и Rio — один WMI Z94 (завод в Петербурге) на две марки.

WMI настоящие, VDS правдоподобные, но выдуманные: каталог кодов
у производителей закрыт, сверить не с чем. Контрольная цифра посчитана
честно, так что VIN проходит проверку.

Каталожные номера — TEST0101 и далее, не сверены. Правдоподобный
выдуманный номер легко принять за настоящий, а «сверенный» ушёл бы
в свою историю подсказки и подставлялся бы в настоящие детали.

Фото — SVG с названием машины или детали. В thumb тот же путь: SVG
масштабируется сам, а пустой thumb подобрал бы rebuild_photos
и попытался открыть вектор как растр.

Всё создаётся тем же путём, что и в приложении: код машины из
donor_code_seq, артикул детали из счётчика машины, паттерн VIN
запоминается learn_vin_pattern — как при приёмке. Удаление находит
созданное по VIN из списка ниже и откатывает то же самое.
"""

import argparse
import asyncio
import dataclasses
import json
import shutil
from datetime import date
from xml.sax.saxutils import escape

from sqlalchemy import text

from ..config import settings
from ..database import dispose, get_session
from ..vin_decoder import check_digit, decode

MARK = "Тестовые данные (app/scripts/seed_test_data.py)."


def make_vin(wmi: str, vds: str, year_code: str, plant: str, serial: str) -> str:
    """VIN с правильной контрольной цифрой в девятой позиции."""
    draft = f"{wmi}{vds}0{year_code}{plant}{serial}"
    return draft[:8] + check_digit(draft) + draft[9:]


# Деталь: категория, название, состояние, пояснение, цена, вес, полка,
# происхождение, бренд (только у неоригинала)
CARS = [
    {
        "vin": make_vin("XTA", "GFL11", "K", "Y", "700101"),
        "brand": "ВАЗ (LADA)", "model": "Vesta", "generation_id": 13301,
        "modification_id": 114094, "complectation": "Comfort",
        "year": 2019, "color": "Серебристый", "mileage_km": 96_000,
        "plate": "К512ТЕ159", "purchase_price": 280_000,
        "accepted_at": date(2026, 8, 20), "branch_id": 1, "status": "dismantling",
        "notes": "Удар в заднюю часть, передок целый.",
        "public_note": "Удар в заднюю часть. Передок, двигатель и коробка целые, салон чистый.",
        "parts": [
            ("Фара левая", "Фара левая", "B", "Линза чистая, потёртость у крепления", 9500, 2.4, "А-1", "original", None),
            ("Дверь передняя правая", "Дверь передняя правая", "C", "Вмятина 3 см на нижней кромке, стекло и замок в комплекте", 12000, 18.5, "В-4", "original", None),
            ("Генератор", "Генератор", "A", "Проверен на стенде, ток отдачи в норме", 6500, 4.6, "Б-2", "original", None),
            ("МКПП", "МКПП 5-ступенчатая", "B", "Без течей, передачи включаются чётко", 28000, 34, "Г-1", "original", None),
            ("Аккумулятор", "Аккумулятор 60 А·ч", "B", "Заряд держит, клеммы целые", 3500, 15.2, "Б-5", "aftermarket", "Varta"),
        ],
    },
    {
        "vin": make_vin("XTA", "GFK33", "P", "Y", "700202"),
        "brand": "ВАЗ (LADA)", "model": "Vesta", "generation_id": 13300,
        "modification_id": 114079, "complectation": "Comfort'24",
        "year": 2023, "color": "Белый", "mileage_km": 38_500,
        "plate": "М047ОР59", "purchase_price": 650_000,
        "accepted_at": date(2026, 8, 28), "branch_id": 2, "status": "dismantling",
        "notes": "После затопления, салон под замену, кузов и агрегаты целые.",
        "public_note": "Была в воде по пороги: салон и электрика под замену, кузов, оптика и агрегаты целые.",
        "parts": [
            ("Бампер передний", "Бампер передний", "B", "Целый, крепления на месте, царапины по низу", 14000, 5.5, "В-1", "original", None),
            ("Капот", "Капот", "A", "Без вмятин и сколов", 16000, 12, "В-2", "original", None),
            ("Вариатор", "Вариатор", "B", "38 тыс. км, по диагностике без ошибок", 115000, 68, "Г-2", "original", None),
            ("Фонарь задний левый", "Фонарь задний левый", "A", "Без трещин и запотевания", 5500, 0.9, "А-3", "original", None),
            ("Тормозной диск передний", "Тормозной диск передний", "A", "Остаток толщины 21 мм", 2200, 5.3, "Б-6", "aftermarket", "Brembo"),
        ],
    },
    {
        "vin": make_vin("XTA", "21074", "S", "0", "700303"),
        "brand": "ВАЗ (LADA)", "model": "2107", "generation_id": 13311,
        "modification_id": 114149, "complectation": "Стандарт",
        "year": 1995, "color": "Белый", "mileage_km": 212_000,
        "plate": "В318НА77", "purchase_price": 35_000,
        "accepted_at": date(2026, 9, 3), "branch_id": 3, "status": "dismantled",
        "notes": "Кузов гнилой, продаём агрегаты и салон.",
        "public_note": "Кузов в коррозии — продаём агрегаты, карданную передачу и салон.",
        "parts": [
            ("Карданный вал", "Карданный вал", "C", "Крестовины без люфта, пыльник подвесного подшипника порван", 3000, 8.5, "Г-3", "original", None),
            ("Стартер", "Стартер", "B", "Крутит уверенно, бендикс заменён", 2200, 3.9, "Б-1", "aftermarket", "BATE"),
            ("Крыло переднее левое", "Крыло переднее левое", "D", "Коррозия по арке, под восстановление", 1500, 3.2, "В-5", "original", None),
            ("Руль", "Руль", "B", "Потёртости обода", 1200, 1.4, "А-6", "original", None),
            ("Сиденье переднее левое", "Сиденье переднее левое", "C", "Обивка протёрта, механизм регулировки исправен", 2500, 11, "Д-1", "original", None),
        ],
    },
    {
        "vin": make_vin("Z94", "K241C", "J", "R", "700404"),
        "brand": "Hyundai", "model": "Solaris", "generation_id": 7910,
        "modification_id": 68102, "complectation": "Elegance",
        "year": 2018, "color": "Чёрный", "mileage_km": 131_000,
        "plate": "Е905КХ799", "purchase_price": 310_000,
        "accepted_at": date(2026, 9, 10), "branch_id": 4, "status": "dismantling",
        "notes": "Удар в левый бок, двигатель и коробка целые.",
        "public_note": "Удар в левый бок. Двигатель, АКПП, передок и правая сторона целые.",
        "parts": [
            ("АКПП", "АКПП 6-ступенчатая", "B", "Переключения без толчков", 55000, 70, "Г-4", "original", None),
            ("Блок управления двигателем", "Блок управления двигателем", "A", "Прошивка заводская", 7000, 0.6, "А-2", "original", None),
            ("Дверь передняя левая", "Дверь передняя левая", "B", "Мелкая вмятина под ручкой, без покраски", 11000, 17.8, "В-3", "original", None),
            ("Фара правая", "Фара правая", "A", "Новая, поставлена перед продажей машины", 6000, 2.5, "А-4", "aftermarket", "Depo"),
            ("Радиатор охлаждения", "Радиатор охлаждения", "B", "Соты целые, опрессован", 4500, 3.1, "Б-3", "original", None),
        ],
    },
    {
        "vin": make_vin("Z94", "C241B", "J", "R", "700505"),
        "brand": "Kia", "model": "Rio", "generation_id": 8301,
        "modification_id": 70194, "complectation": "Luxe",
        "year": 2018, "color": "Красный", "mileage_km": 104_000,
        "plate": "Т264УМ159", "purchase_price": 290_000,
        "accepted_at": date(2026, 9, 15), "branch_id": 1, "status": "dismantled",
        "notes": "Удар в переднюю часть, зад и коробка целые.",
        "public_note": "Удар в переднюю часть. Задняя часть, салон и МКПП целые.",
        "parts": [
            ("МКПП", "МКПП 6-ступенчатая", "B", "Без хруста, сальники сухие", 32000, 36, "Г-5", "original", None),
            ("Бампер задний", "Бампер задний", "C", "Трещина у левого крепления, под ремонт", 6000, 4.8, "В-6", "original", None),
            ("Фонарь задний правый", "Фонарь задний правый", "A", "Без трещин", 5000, 1.0, "А-5", "original", None),
            ("Решётка радиатора", "Решётка радиатора", "B", "Хром без сколов, одно крепление подклеено", 3500, 1.1, "А-7", "original", None),
            ("Зеркало правое", "Зеркало правое", "B", "С подогревом, электропривод работает", 3000, 1.3, "А-8", "aftermarket", "TYC"),
        ],
    },
]

VINS = [c["vin"] for c in CARS]


# ------------------------------------------------------------------
# SVG вместо фото
# ------------------------------------------------------------------


def wrap(line: str, width: int) -> list[str]:
    """Перенос по словам: длинное название в одну строку не влезет."""
    out, cur = [], ""
    for word in line.split():
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return out + ([cur] if cur else [])


def svg(title: str, subtitle: str, kind: str) -> str:
    lines = wrap(title, 20)[:3]
    size = 84 if len(lines) == 1 else 70
    top = 450 - (len(lines) - 1) * size * 0.6
    title_svg = "".join(
        f'<text x="600" y="{top + i * size * 1.2:.0f}" font-size="{size}" '
        f'font-weight="700" fill="#F5F7FA" text-anchor="middle">{escape(t)}</text>'
        for i, t in enumerate(lines)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="900" viewBox="0 0 1200 900"
     font-family="Arial, Helvetica, sans-serif">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{'#1F3A5F' if kind == 'car' else '#2B2F36'}"/>
      <stop offset="1" stop-color="{'#0E1B2C' if kind == 'car' else '#15181D'}"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="900" fill="url(#bg)"/>
  <rect x="40" y="40" width="1120" height="820" rx="28" fill="none"
        stroke="#FFC233" stroke-opacity=".35" stroke-width="3" stroke-dasharray="14 10"/>
  <text x="600" y="150" font-size="30" letter-spacing="6" fill="#FFC233"
        text-anchor="middle">{'МАШИНА' if kind == 'car' else 'ДЕТАЛЬ'}</text>
  {title_svg}
  <text x="600" y="{top + len(lines) * size * 1.2 + 40:.0f}" font-size="36" fill="#AEB6C2"
        text-anchor="middle">{escape(subtitle)}</text>
  <text x="600" y="815" font-size="26" letter-spacing="4" fill="#6B7480"
        text-anchor="middle">ТЕСТОВОЕ ФОТО</text>
</svg>
"""


def write_svg(folder, name: str, body: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(body, encoding="utf-8")
    return f"/media/{folder.relative_to(settings.media_root).as_posix()}/{name}"


# ------------------------------------------------------------------
# Создание
# ------------------------------------------------------------------


async def ref_names(s, car) -> str:
    row = (await s.execute(text("""
        SELECT b.name AS brand, m.name AS model, g.name AS gen
          FROM generations g JOIN models m ON m.id = g.model_id
          JOIN brands b ON b.id = m.brand_id
         WHERE g.id = :g"""), {"g": car["generation_id"]})).first()
    if not row or row.brand != car["brand"] or row.model != car["model"]:
        raise SystemExit(f"В справочнике нет поколения {car['generation_id']} для "
                         f"{car['brand']} {car['model']} — поправьте список CARS")
    return f"{row.brand} {row.model} {row.gen}"


async def create(s, car, apply: bool) -> None:
    title = await ref_names(s, car)
    exists = (await s.execute(text("SELECT code FROM donors WHERE vin = :v"),
                              {"v": car["vin"]})).scalar()
    if exists:
        print(f"  {car['vin']}  уже есть ({exists}) — пропускаю")
        return

    compl = (await s.execute(text("""
        SELECT id FROM complectations WHERE modification_id = :m AND name = :n
         ORDER BY sort_order LIMIT 1"""),
        {"m": car["modification_id"], "n": car["complectation"]})).scalar()
    if compl is None:
        raise SystemExit(f"Нет комплектации «{car['complectation']}» у модификации "
                         f"{car['modification_id']}")

    print(f"  {car['vin']}  {title}, {car['year']}, {len(car['parts'])} дет.")
    if not apply:
        return

    info = decode(car["vin"])
    donor = (await s.execute(text("""
        INSERT INTO donors (code, vin, generation_id, modification_id, complectation_id,
                            year, color, mileage_km, plate, purchase_price, accepted_at,
                            notes, public_note, vin_source, vin_decoded, branch_id)
        VALUES ('D-' || lpad(nextval('donor_code_seq')::text, 4, '0'),
                :vin, :gen, :mod, :compl, :year, :color, :mileage, :plate, :price,
                :accepted, :notes, :public_note, 'manual', CAST(:decoded AS jsonb), :branch)
        RETURNING id, code"""), {
        "vin": car["vin"], "gen": car["generation_id"], "mod": car["modification_id"],
        "compl": compl, "year": car["year"], "color": car["color"],
        "mileage": car["mileage_km"], "plate": car["plate"], "price": car["purchase_price"],
        "accepted": car["accepted_at"], "notes": f"{car['notes']} {MARK}",
        "public_note": car["public_note"],
        "decoded": json.dumps(dataclasses.asdict(info), ensure_ascii=False),
        "branch": car["branch_id"],
    })).first()

    # Как при приёмке: VIN и модификация известны — паттерн запоминается
    await s.execute(text("SELECT learn_vin_pattern(:w, :v, :m, NULL)"),
                    {"w": car["vin"][:3], "v": car["vin"][3:8], "m": car["modification_id"]})

    path = write_svg(settings.media_root / "donors" / str(donor.id), "test.svg",
                     svg(f"{car['brand'].replace('ВАЗ (LADA)', 'LADA')} {car['model']}",
                         f"{car['year']} · {donor.code}", "car"))
    await s.execute(text("""
        INSERT INTO donor_photos (donor_id, path, thumb, width, height, sort_order)
        VALUES (:d, :p, :p, 1200, 900, 0)"""), {"d": donor.id, "p": path})

    for n, (cat_name, name, cond, note, price, weight, loc, origin, brand) in enumerate(car["parts"], 1):
        cat = (await s.execute(text("SELECT id FROM part_categories WHERE name = :n"),
                               {"n": cat_name})).scalar()
        if cat is None:
            raise SystemExit(f"Нет категории «{cat_name}»")

        # Артикул — из счётчика машины, как в dismantle.py
        sku = (await s.execute(text("""
            UPDATE donors SET part_counter = part_counter + 1 WHERE id = :d
            RETURNING code || '-' || lpad(part_counter::text, 4, '0')"""),
            {"d": donor.id})).scalar()

        part_id = (await s.execute(text("""
            INSERT INTO parts (sku, donor_id, category_id, name, oem_number, condition,
                               condition_note, price, status, location, weight_kg,
                               published, source, branch_id, oem_source, oem_verified,
                               origin, part_brand)
            VALUES (:sku, :d, :cat, :name, :oem, CAST(:cond AS part_condition), :note,
                    :price, 'in_stock', :loc, :weight, true, 'donor', :branch,
                    'test', false, :origin, :brand)
            RETURNING id"""), {
            "sku": sku, "d": donor.id, "cat": cat, "name": name,
            "oem": f"TEST{CARS.index(car) + 1:02d}{n:02d}", "cond": cond, "note": note,
            "price": price, "loc": loc, "weight": weight, "branch": car["branch_id"],
            "origin": origin, "brand": brand,
        })).scalar()

        path = write_svg(settings.media_root / "parts" / str(part_id), "test.svg",
                         svg(name, f"{car['model']} {car['year']} · {sku}", "part"))
        await s.execute(text("""
            INSERT INTO part_photos (part_id, path, thumb, width, height, sort_order)
            VALUES (:p, :path, :path, 1200, 900, 0)"""), {"p": part_id, "path": path})

    await s.execute(text("UPDATE donors SET status = CAST(:st AS donor_status) WHERE id = :d"),
                    {"st": car["status"], "d": donor.id})
    await s.commit()
    print(f"      создана {donor.code}")


# ------------------------------------------------------------------
# Удаление
# ------------------------------------------------------------------


async def remove(s, apply: bool) -> None:
    donors = (await s.execute(text("""
        SELECT id, code, vin, modification_id FROM donors WHERE vin = ANY(:v)"""),
        {"v": VINS})).all()
    if not donors:
        print("  тестовых машин нет")
        return
    ids = [d.id for d in donors]
    parts = (await s.execute(text("SELECT id FROM parts WHERE donor_id = ANY(:d)"),
                             {"d": ids})).scalars().all()

    # Заказ на тестовую деталь удалять молча нельзя: это уже чужие данные
    ordered = (await s.execute(text("""
        SELECT DISTINCT o.number FROM order_items oi JOIN orders o ON o.id = oi.order_id
         WHERE oi.part_id = ANY(:p)"""), {"p": parts})).scalars().all()
    if ordered:
        raise SystemExit(f"На тестовые детали есть заказы: {', '.join(map(str, ordered))}. "
                         "Сначала удалите или отмените их — скрипт их не трогает.")

    print(f"  машин {len(donors)} ({', '.join(d.code for d in donors)}), деталей {len(parts)}")
    if not apply:
        return

    for d in donors:
        # Паттерн мог подтвердить и кто-то ещё — снимаем только наш голос
        await s.execute(text("""
            UPDATE vin_patterns SET hits = hits - 1
             WHERE wmi = :w AND vds = :v AND modification_id = :m"""),
            {"w": d.vin[:3], "v": d.vin[3:8], "m": d.modification_id})
    await s.execute(text("DELETE FROM vin_patterns WHERE hits <= 0"))
    await s.execute(text("DELETE FROM vin_queries WHERE vin = ANY(:v)"), {"v": VINS})
    await s.execute(text("DELETE FROM parts WHERE id = ANY(:p)"), {"p": parts})
    await s.execute(text("DELETE FROM donors WHERE id = ANY(:d)"), {"d": ids})
    await s.commit()

    for pid in parts:
        shutil.rmtree(settings.media_root / "parts" / str(pid), ignore_errors=True)
    for did in ids:
        shutil.rmtree(settings.media_root / "donors" / str(did), ignore_errors=True)
    print("  удалено")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="без него только показывает")
    ap.add_argument("--remove", action="store_true", help="убрать созданное")
    a = ap.parse_args()

    agen = get_session()
    s = await agen.__anext__()
    try:
        if a.remove:
            print("Удаление тестовых данных" + ("" if a.apply else " (проверка, --apply чтобы удалить)"))
            await remove(s, a.apply)
        else:
            print("Тестовые данные" + ("" if a.apply else " (проверка, --apply чтобы создать)"))
            for car in CARS:
                await create(s, car, a.apply)
    finally:
        await agen.aclose()
        await dispose()


if __name__ == "__main__":
    asyncio.run(main())
