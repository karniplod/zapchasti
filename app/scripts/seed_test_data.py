"""Тестовые данные: машины всех статусов и типов, детали всех видов.

    python -m app.scripts.seed_test_data                    покажет, что будет создано
    python -m app.scripts.seed_test_data --apply            создаст
    python -m app.scripts.seed_test_data --remove           покажет, что будет удалено
    python -m app.scripts.seed_test_data --remove --apply   уберёт всё, что создал

Что покрыто.

Машины — все статусы (ждёт разбора, в разборе, разобрана, утилизирована),
с VIN и без (праворульный японец с номером кузова), кузова от седана до
пикапа и фургона, бензин, дизель, газ, гибрид, электро; механика,
автомат, вариатор, робот; передний, задний и полный привод. У каждой
машины описание для покупателя (public_note) и внутренняя заметка.

Детали — снятые с машины и принятые вручную (куплена б/у, новая)
с применимостью к нескольким поколениям; все статусы (черновик без фото,
в наличии, бронь, продана, списана); без цены — не опубликована;
оригинал, ОЕМ, аналог с брендом; состояние A–D; с каталожным номером
и без. У каждой детали — пояснение к состоянию.

Машины подобраны и под слабые места декодера VIN:
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
donor_code_seq, артикул детали из счётчика машины (у ручной — из
standalone_part_seq, P-0001), паттерн VIN запоминается learn_vin_pattern —
как при приёмке. Удаление находит машины по пометке в заметке, ручные
детали — по номеру TESTM…, и откатывает то же самое.
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
    # Пикап, дизель, автомат, полный привод. Ждёт разбора — деталей
    # ещё нет, все на машине. WMI MR0 (Таиланд) декодеру незнаком
    {
        "vin": make_vin("MR0", "FB3CD", "M", "5", "700606"),
        "brand": "Toyota", "model": "Hilux", "generation_id": 12638,
        "modification_id": 107165, "complectation": "DLX",
        "year": 2021, "color": "Серый", "mileage_km": 74_000,
        "plate": "А606ВС159", "purchase_price": 1_200_000,
        "accepted_at": date(2026, 9, 18), "branch_id": 2, "status": "accepted",
        "notes": "Опрокидывание, крыша и кабина под замену, рама ровная.",
        "public_note": "После опрокидывания: кабина и крыша под замену. Рама ровная, "
                       "двигатель, коробка, раздатка и мосты целые. Разбор начнём на "
                       "этой неделе — нужное снимем под заказ.",
        "parts": [],
    },
    # Лифтбек, гибрид, вариатор. Праворульный японец без VIN — только
    # номер кузова. Детали во всех статусах
    {
        "vin": None,
        "brand": "Toyota", "model": "Prius", "generation_id": 12586,
        "modification_id": 106691, "complectation": "Base",
        "year": 2013, "color": "Белый", "mileage_km": 168_000,
        "plate": "О707ОО77", "purchase_price": 240_000,
        "accepted_at": date(2026, 9, 5), "branch_id": 3, "status": "dismantling",
        "notes": "Праворульный, из Японии, VIN нет — кузов ZVW30. Удар в перед.",
        "public_note": "Праворульная, из Японии: VIN нет, номер кузова ZVW30. Удар "
                       "в переднюю часть. Гибридная система на ходу, задняя часть "
                       "и салон целые.",
        "parts": [
            ("Фара левая", "Фара левая", "A", "Линза без помутнения, крепления целые",
             11000, 2.2, "Д-2", "original", None),
            ("Блок управления двигателем", "Блок управления двигателем", "B",
             "Проверен на машине, ошибок нет", 9000, 0.8, "А-9", "original", None,
             {"status": "reserved"}),
            ("Бампер передний", "Бампер передний", "C", "Трещина у противотуманной фары, под пайку",
             7000, 4.5, "В-7", "original", None, {"status": "sold"}),
            ("Дверь задняя левая", "Дверь задняя левая", "B", "Снята сегодня, ждёт фото",
             9000, 16, "В-8", "original", None, {"status": "draft"}),
            ("Руль", "Руль", "B", "Кожа потёрта на хвате, кнопки работают",
             None, 1.6, "А-10", "original", None, {"oem": None}),
            ("Аккумулятор", "Аккумулятор вспомогательный 12 В", "D", "Не держит заряд — списан",
             1500, 8, "Б-7", "original", None, {"status": "written_off"}),
        ],
    },
    # Хетчбэк, робот. Утилизирована: на витрине машины нет, но то, что
    # с неё сняли и не продали, по-прежнему в каталоге
    {
        "vin": make_vin("XW8", "ZZZ6R", "F", "W", "700707"),
        "brand": "Volkswagen", "model": "Polo", "generation_id": 12823,
        "modification_id": 109666, "complectation": "Highline",
        "year": 2015, "color": "Синий", "mileage_km": 187_000,
        "plate": "Н115РХ799", "purchase_price": 190_000,
        "accepted_at": date(2026, 7, 14), "branch_id": 4, "status": "scrapped",
        "notes": "Кузов сдан на металл 10.09.",
        "public_note": "Разобрана полностью, кузов сдан на металл. Оставшиеся детали — "
                       "в каталоге.",
        "parts": [
            ("АКПП", "Коробка DSG 7 (робот)", "B", "Мехатроник исправен, сцепление 60%",
             45000, 70, "Г-6", "original", None, {"status": "sold"}),
            ("Капот", "Капот", "D", "Замят при эвакуации — в утиль",
             None, 11, "В-9", "original", None, {"status": "written_off", "oem": None}),
            ("Зеркало левое", "Зеркало левое", "A", "С подогревом и повторителем поворота",
             3500, 1.2, "А-11", "original", None),
            ("Катушка зажигания", "Катушки зажигания, 4 шт.", "B", "Комплектом, проверены",
             4000, 0.8, "Б-8", "oem", "Bosch"),
        ],
    },
    # Внедорожник, механика, полный привод. WMI XTT (УАЗ) декодеру незнаком
    {
        "vin": make_vin("XTT", "31637", "L", "0", "700808"),
        "brand": "УАЗ", "model": "Patriot", "generation_id": 13494,
        "modification_id": 115206, "complectation": "Оптимум",
        "year": 2020, "color": "Зелёный", "mileage_km": 61_000,
        "plate": "Р808КМ159", "purchase_price": 520_000,
        "accepted_at": date(2026, 9, 8), "branch_id": 1, "status": "dismantling",
        "notes": "Утоплен в реке, электрика вся под замену.",
        "public_note": "Побывал в воде: электрика и салон под замену. Кузов без "
                       "коррозии, раздатка, мосты и рулевое в порядке.",
        "parts": [
            ("Раздаточная коробка", "Раздаточная коробка", "B", "Без течей, передний мост подключается",
             38000, 32, "Г-7", "oem", "Dymos"),
            ("Рулевая рейка", "Рулевая рейка", "C", "Люфт в пределах нормы, пыльники порваны",
             12000, 9, "Г-8", "original", None, {"oem": None}),
            ("Фаркоп", "Фаркоп", "B", "Шар и розетка целые, крепёж в комплекте",
             4500, 15, "Д-3", "aftermarket", "Бизон"),
            ("Дверь задняя правая", "Дверь задняя правая", "A", "Без вмятин, стекло целое",
             14000, 22, "В-10", "original", None, {"status": "reserved"}),
            ("Компрессор кондиционера", "Компрессор кондиционера", "B", "Проверен на давление",
             13000, 6, "Б-9", "original", None),
        ],
    },
    # Хетчбэк, электро. Разобрана. WMI SJN (Nissan, Великобритания)
    {
        "vin": make_vin("SJN", "FAAZE", "J", "U", "700909"),
        "brand": "Nissan", "model": "Leaf", "generation_id": 9960,
        "modification_id": 83863, "complectation": "Tekna",
        "year": 2018, "color": "Красный", "mileage_km": 93_000,
        "plate": "Е909ЕЕ799", "purchase_price": 610_000,
        "accepted_at": date(2026, 8, 2), "branch_id": 4, "status": "dismantled",
        "notes": "Батарея продана целиком отдельно, в систему не заводили.",
        "public_note": "Электромобиль после удара в заднюю часть. Тяговая батарея "
                       "уже продана, остальное — в списке ниже.",
        "parts": [
            ("Фонарь задний правый", "Фонарь задний правый", "A", "Без трещин",
             6500, 0.9, "А-12", "original", None, {"status": "sold"}),
            ("Сиденье переднее правое", "Сиденье переднее правое", "B",
             "Подогрев работает, ткань чистая", 8000, 18, "Д-4", "original", None),
            ("Стеклоподъёмник", "Стеклоподъёмник передний левый", "B", "С мотором, работает",
             3000, 1.8, "Б-10", "original", None),
            ("Радиатор охлаждения", "Радиатор охлаждения", "C", "Погнуты соты по краю",
             2500, 3, "Б-11", "original", None),
        ],
    },
    # Фургон на газу, механика
    {
        "vin": make_vin("XTA", "RS045", "H", "0", "701010"),
        "brand": "ВАЗ (LADA)", "model": "Largus", "generation_id": 13324,
        "modification_id": 114222, "complectation": "Luxe",
        "year": 2017, "color": "Белый", "mileage_km": 246_000,
        "plate": "К010КК59", "purchase_price": 210_000,
        "accepted_at": date(2026, 9, 12), "branch_id": 2, "status": "dismantling",
        "notes": "Газовое оборудование сняли и продали отдельно.",
        "public_note": "Коммерческий фургон, большой пробег. Удар в правый бок; "
                       "двигатель, коробка и задние двери целые.",
        "parts": [
            ("Топливный насос", "Топливный насос", "B", "Работает тихо, давление в норме",
             2500, 1.5, "Б-12", "original", None),
            ("Глушитель", "Глушитель", "C", "Прогар у задней банки",
             1500, 9, "Д-5", "original", None, {"oem": None}),
            ("Дверь задняя левая", "Дверь задняя левая (распашная)", "B", "Мелкие вмятины, петли целые",
             9000, 20, "В-11", "original", None),
            ("Генератор", "Генератор", "A", "Поставлен за месяц до ДТП",
             7000, 5, "Б-13", "aftermarket", "Kraftwerk"),
        ],
    },

]

VINS = [c["vin"] for c in CARS if c["vin"]]

# Детали, принятые вручную — не с нашей машины. Применимость указана
# списком поколений, как в «Приёме детали». Номер TESTM… — по нему
# удаление их и находит. Поля те же, что у снятых, плюс источник
# (purchased — куплена б/у, new — новая), филиал и поколения
MANUAL = [
    ("Шина", "Шина зимняя 205/55 R16", "B", "Остаток шипов около 90%, без грыж и порезов",
     3500, 9, "Ш-1", "aftermarket", "Nokian", "purchased", 1, [13301, 12823, 7910, 8301], {}),
    ("Диск литой", "Диск литой R15 4×100", "B", "Бордюрный скол на ободе, геометрия в норме",
     4000, 7, "Ш-2", "aftermarket", "Replica", "purchased", 1, [7910, 8301], {}),
    ("Магнитола", "Магнитола 2DIN", "A", "Bluetooth, USB, рамка в комплекте",
     4500, 1.2, "А-13", "aftermarket", "Pioneer", "purchased", 2, [13301, 13300, 13324], {}),
    ("Тормозной диск передний", "Тормозной диск передний", "A", "Новый, в заводской упаковке",
     2800, 5.2, "Б-14", "oem", "TRW", "new", 3, [13301, 13300], {}),
    ("Фара левая", "Фара левая", "B", "Сняла сторонняя разборка, крепления целые",
     7500, 2.3, "А-14", "original", None, "purchased", 1, [8301], {"status": "reserved"}),
    ("Генератор", "Генератор", "B", "Принят вчера, ждёт фото",
     5000, 5, "Б-15", "oem", "Valeo", "purchased", 2, [13324], {"status": "draft"}),
    ("Фаркоп", "Фаркоп", "B", "Без электрики, крепёж неполный — цену уточняем",
     None, 16, "Д-6", "aftermarket", "Лидер Плюс", "purchased", 1, [13494], {}),
    ("Камера заднего вида", "Камера заднего вида", "A", "Новая, в коробке",
     2000, 0.3, "А-15", "aftermarket", "Interpower", "new", 4, [13301, 13300, 12823],
     {"status": "sold"}),
    ("Домкрат", "Домкрат штатный", "A", "Штатный, из комплекта машины",
     1500, 3, "Д-7", "original", None, "purchased", 3, [12638, 12586], {}),
]


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
    # 15 знаков в строке, а не 20: картинка 4:3, а плитка на витрине
    # квадратная — бока обрезаются, и длинное название теряло края
    lines = wrap(title, 15)[:3]
    size = 76 if len(lines) == 1 else 64
    top = 450 - (len(lines) - 1) * size * 0.6
    title_svg = "".join(
        f'<text x="600" y="{top + i * size * 1.2:.0f}" font-size="{size}" '
        f'font-weight="700" fill="#22252B" text-anchor="middle">{escape(t)}</text>'
        for i, t in enumerate(lines)
    )
    # Светлая заглушка, а не тёмная: на витрине снимок детали лежит на
    # белой карточке, и тёмный прямоугольник вместо него перетягивал
    # на себя всю страницу — витрина выглядела чёрной сеткой
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="900" viewBox="0 0 1200 900"
     font-family="Arial, Helvetica, sans-serif">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#FFFFFF"/>
      <stop offset="1" stop-color="{'#EDF1F6' if kind == 'car' else '#F1F0EE'}"/>
    </linearGradient>
  </defs>
  <rect width="1200" height="900" fill="url(#bg)"/>
  <rect x="40" y="40" width="1120" height="820" rx="28" fill="none"
        stroke="#F54F0C" stroke-opacity=".22" stroke-width="3" stroke-dasharray="14 10"/>
  <text x="600" y="150" font-size="30" letter-spacing="6" fill="#F54F0C"
        text-anchor="middle">{'МАШИНА' if kind == 'car' else 'ДЕТАЛЬ'}</text>
  {title_svg}
  <text x="600" y="{top + len(lines) * size * 1.2 + 40:.0f}" font-size="36" fill="#6E6E6E"
        text-anchor="middle">{escape(subtitle)}</text>
  <text x="600" y="815" font-size="26" letter-spacing="4" fill="#A9A9A9"
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
    # Машину без VIN узнаём по госномеру и пометке тестовых данных
    exists = (await s.execute(
        text("SELECT code FROM donors WHERE vin = :v")
        if car["vin"] else
        text("SELECT code FROM donors WHERE plate = :p AND notes LIKE '%' || :m"),
        {"v": car["vin"]} if car["vin"] else {"p": car["plate"], "m": MARK})).scalar()
    label = car["vin"] or f"без VIN ({car['plate']})"
    if exists:
        print(f"  {label}  уже есть ({exists}) — пропускаю")
        return

    compl = (await s.execute(text("""
        SELECT id FROM complectations WHERE modification_id = :m AND name = :n
         ORDER BY sort_order LIMIT 1"""),
        {"m": car["modification_id"], "n": car["complectation"]})).scalar()
    if compl is None:
        raise SystemExit(f"Нет комплектации «{car['complectation']}» у модификации "
                         f"{car['modification_id']}")

    print(f"  {label}  {title}, {car['year']}, {len(car['parts'])} дет.")
    if not apply:
        return

    info = decode(car["vin"]) if car["vin"] else None
    donor = (await s.execute(text("""
        INSERT INTO donors (code, vin, generation_id, modification_id, complectation_id,
                            year, color, mileage_km, plate, purchase_price, accepted_at,
                            notes, public_note, vin_source, vin_decoded, branch_id)
        VALUES ('D-' || lpad(nextval('donor_code_seq')::text, 4, '0'),
                :vin, :gen, :mod, :compl, :year, :color, :mileage, :plate, :price,
                :accepted, :notes, :public_note, :src, CAST(:decoded AS jsonb), :branch)
        RETURNING id, code"""), {
        "vin": car["vin"], "gen": car["generation_id"], "mod": car["modification_id"],
        "compl": compl, "year": car["year"], "color": car["color"],
        "mileage": car["mileage_km"], "plate": car["plate"], "price": car["purchase_price"],
        "accepted": car["accepted_at"], "notes": f"{car['notes']} {MARK}",
        "public_note": car["public_note"],
        # Как при приёмке: без VIN — источник no_vin и нечего расшифровывать
        "src": "manual" if car["vin"] else "no_vin",
        "decoded": json.dumps(dataclasses.asdict(info), ensure_ascii=False) if info else None,
        "branch": car["branch_id"],
    })).first()

    # Как при приёмке: VIN и модификация известны — паттерн запоминается
    if car["vin"]:
        await s.execute(text("SELECT learn_vin_pattern(:w, :v, :m, NULL)"),
                        {"w": car["vin"][:3], "v": car["vin"][3:8],
                         "m": car["modification_id"]})

    path = write_svg(settings.media_root / "donors" / str(donor.id), "test.svg",
                     svg(f"{car['brand'].replace('ВАЗ (LADA)', 'LADA')} {car['model']}",
                         f"{car['year']} · {donor.code}", "car"))
    await s.execute(text("""
        INSERT INTO donor_photos (donor_id, path, thumb, width, height, sort_order)
        VALUES (:d, :p, :p, 1200, 900, 0)"""), {"d": donor.id, "p": path})

    for n, row in enumerate(car["parts"], 1):
        cat_name, name, cond, note, price, weight, loc, origin, brand = row[:9]
        extra = row[9] if len(row) > 9 else {}
        status = extra.get("status", "in_stock")
        oem = extra.get("oem", f"TEST{CARS.index(car) + 1:02d}{n:02d}")
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
                    :price, CAST(:status AS part_status), :loc, :weight, :pub, 'donor',
                    :branch, :oem_src, false, :origin, :brand)
            RETURNING id"""), {
            "sku": sku, "d": donor.id, "cat": cat, "name": name,
            "oem": oem, "oem_src": "test" if oem else None,
            "cond": cond, "note": note, "price": price, "status": status,
            # Как в приложении: опубликована, если есть и фото, и цена.
            # Бронь, продажа, списание флаг не трогают — каталог смотрит статус
            "pub": status != "draft" and price is not None,
            "loc": loc, "weight": weight, "branch": car["branch_id"],
            "origin": origin, "brand": brand,
        })).scalar()

        # Черновик — это деталь без фото: так его и заводят
        if status != "draft":
            await add_photo(s, part_id, name, f"{car['model']} {car['year']} · {sku}")

    await s.execute(text("UPDATE donors SET status = CAST(:st AS donor_status) WHERE id = :d"),
                    {"st": car["status"], "d": donor.id})
    await s.commit()
    print(f"      создана {donor.code}")


async def add_photo(s, part_id: int, title: str, subtitle: str) -> None:
    path = write_svg(settings.media_root / "parts" / str(part_id), "test.svg",
                     svg(title, subtitle, "part"))
    await s.execute(text("""
        INSERT INTO part_photos (part_id, path, thumb, width, height, sort_order)
        VALUES (:p, :path, :path, 1200, 900, 0)"""), {"p": part_id, "path": path})


async def create_manual(s, n: int, row, apply: bool) -> None:
    """Деталь со стороны — как «Приём детали»: артикул P-…, применимость
    списком поколений, филиал приёмщика."""
    (cat_name, name, cond, note, price, weight, loc, origin, brand,
     source, branch, gens, extra) = row
    oem = f"TESTM{n:02d}"
    exists = (await s.execute(text("SELECT sku FROM parts WHERE oem_number = :o"),
                              {"o": oem})).scalar()
    if exists:
        print(f"  {oem}  уже есть ({exists}) — пропускаю")
        return
    cat = (await s.execute(text("SELECT id FROM part_categories WHERE name = :n"),
                           {"n": cat_name})).scalar()
    if cat is None:
        raise SystemExit(f"Нет категории «{cat_name}»")
    status = extra.get("status", "in_stock")
    print(f"  {oem}  {name} — {'новая' if source == 'new' else 'куплена б/у'}, {status}, "
          f"поколений {len(gens)}")
    if not apply:
        return

    sku = (await s.execute(text(
        "SELECT 'P-' || lpad(nextval('standalone_part_seq')::text, 4, '0')"))).scalar()
    part_id = (await s.execute(text("""
        INSERT INTO parts (sku, donor_id, category_id, name, oem_number, condition,
                           condition_note, price, status, location, weight_kg,
                           published, source, branch_id, oem_source, oem_verified,
                           origin, part_brand)
        VALUES (:sku, NULL, :cat, :name, :oem, CAST(:cond AS part_condition), :note,
                :price, CAST(:status AS part_status), :loc, :weight, :pub, :src,
                :branch, 'test', false, :origin, :brand)
        RETURNING id"""), {
        "sku": sku, "cat": cat, "name": name, "oem": oem, "cond": cond, "note": note,
        "price": price, "status": status, "pub": status != "draft" and price is not None,
        "loc": loc, "weight": weight, "src": source, "branch": branch,
        "origin": origin, "brand": brand,
    })).scalar()
    for g in gens:
        await s.execute(text("""
            INSERT INTO part_applicability (part_id, generation_id) VALUES (:p, :g)
            ON CONFLICT DO NOTHING"""), {"p": part_id, "g": g})
    if status != "draft":
        await add_photo(s, part_id, name, f"приём детали · {sku}")
    await s.commit()
    print(f"      создана {sku}")


# ------------------------------------------------------------------
# Удаление
# ------------------------------------------------------------------


async def remove(s, apply: bool) -> None:
    # По пометке в заметке — так находится и машина без VIN
    donors = (await s.execute(text("""
        SELECT id, code, vin, modification_id FROM donors
         WHERE vin = ANY(:v) OR notes LIKE '%' || :m"""),
        {"v": VINS, "m": MARK})).all()
    ids = [d.id for d in donors]
    parts = (await s.execute(text("""
        SELECT id FROM parts
         WHERE donor_id = ANY(:d) OR (donor_id IS NULL AND oem_number LIKE 'TESTM%')"""),
        {"d": ids})).scalars().all()
    if not donors and not parts:
        print("  тестовых данных нет")
        return

    # Заказ на тестовую деталь удалять молча нельзя: это уже чужие данные
    ordered = (await s.execute(text("""
        SELECT DISTINCT o.number FROM order_items oi JOIN orders o ON o.id = oi.order_id
         WHERE oi.part_id = ANY(:p)"""), {"p": parts})).scalars().all()
    if ordered:
        raise SystemExit(f"На тестовые детали есть заказы: {', '.join(map(str, ordered))}. "
                         "Сначала удалите или отмените их — скрипт их не трогает.")

    print(f"  машин {len(donors)} ({', '.join(d.code for d in donors)}), "
          f"деталей {len(parts)} (с машин и принятых вручную)")
    if not apply:
        return

    for d in donors:
        if not d.vin:
            continue
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
            print("Детали, принятые вручную")
            for n, row in enumerate(MANUAL, 1):
                await create_manual(s, n, row, a.apply)
    finally:
        await agen.aclose()
        await dispose()


if __name__ == "__main__":
    asyncio.run(main())
