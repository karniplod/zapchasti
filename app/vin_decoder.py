"""
Собственный VIN-декодер для автодонора.

Что он умеет БЕЗ внешних API:
  - проверить корректность VIN (длина, запрещённые символы, контрольная цифра)
  - определить страну и завод-изготовитель (WMI, позиции 1-3)
  - определить модельный год (позиция 10)
  - подставить модификацию по накопленным VDS-паттернам (позиции 4-8)

Чего он НЕ умеет и не сможет: расшифровать модель/двигатель/КПП
для нового, ранее не встречавшегося VDS. Позиции 4-8 не стандартизованы —
каждый производитель кодирует их по-своему и каталоги не публикует.
Поэтому первый автомобиль каждой новой модели оператор заводит руками,
а система запоминает паттерн (learn_pattern) и дальше подставляет сама.
"""

from dataclasses import dataclass, field
from datetime import date

# ------------------------------------------------------------------
# Константы стандарта ISO 3779
# ------------------------------------------------------------------

FORBIDDEN = set("IOQ")  # никогда не встречаются в VIN
VALID_CHARS = set("0123456789ABCDEFGHJKLMNPRSTUVWXYZ")

# Транслитерация для контрольной цифры (позиция 9)
TRANSLIT = {
    **{str(d): d for d in range(10)},
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5,
    "F": 6,
    "G": 7,
    "H": 8,
    "J": 1,
    "K": 2,
    "L": 3,
    "M": 4,
    "N": 5,
    "P": 7,
    "R": 9,
    "S": 2,
    "T": 3,
    "U": 4,
    "V": 5,
    "W": 6,
    "X": 7,
    "Y": 8,
    "Z": 9,
}

WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]

# Позиция 10 -> модельный год. Цикл 30 лет.
YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"  # A=1980 ... 9=2009, далее A=2010

# Первый символ VIN -> регион/страна (это часть стандарта, надёжно)
COUNTRY_RANGES = [
    ("A", "H", "Африка"),
    ("J", "J", "Япония"),
    ("K", "K", "Корея"),
    ("L", "L", "Китай"),
    ("M", "M", "Индия / Индонезия / Таиланд"),
    ("N", "N", "Иран / Турция"),
    ("P", "P", "Филиппины / Сингапур / Малайзия"),
    ("R", "R", "ОАЭ / Тайвань / Вьетнам"),
    ("S", "S", "Великобритания"),
    ("T", "T", "Швейцария / Чехия / Венгрия"),
    ("U", "U", "Словакия / Румыния"),
    ("V", "V", "Франция / Испания / Австрия"),
    ("W", "W", "Германия"),
    ("X", "X", "Россия / СНГ / Болгария"),
    ("Y", "Y", "Швеция / Финляндия / Бельгия / Норвегия"),
    ("Z", "Z", "Италия"),
    ("1", "1", "США"),
    ("2", "2", "Канада"),
    ("3", "3", "Мексика"),
    ("4", "5", "США"),
    ("6", "6", "Австралия"),
    ("7", "7", "Новая Зеландия"),
    ("8", "8", "Аргентина / Чили"),
    ("9", "9", "Бразилия"),
]

# Стартовый справочник WMI. Пополняется из БД — здесь только затравка
# для самых частых на разборе марок.
WMI_SEED = {
    "XTA": "LADA (АвтоВАЗ)",
    "X7L": "Renault Россия",
    "XW8": "Volkswagen Group Rus",
    "Z94": "Hyundai Россия",
    "XWB": "Ravon / Uz-Daewoo",
    "WVW": "Volkswagen",
    "WV1": "Volkswagen Commercial",
    "WAU": "Audi",
    "WBA": "BMW",
    "WBS": "BMW M",
    "WDB": "Mercedes-Benz",
    "WDD": "Mercedes-Benz",
    "WP0": "Porsche",
    "TMB": "Skoda",
    "VF1": "Renault",
    "VF3": "Peugeot",
    "VF7": "Citroen",
    "YV1": "Volvo",
    "SAL": "Land Rover",
    "SAJ": "Jaguar",
    "KMH": "Hyundai",
    "KNA": "Kia",
    "KND": "Kia",
    "JT2": "Toyota",
    "JTD": "Toyota",
    "JTH": "Lexus",
    "JHM": "Honda",
    "JHL": "Honda",
    "JN1": "Nissan",
    "JN8": "Nissan",
    "JMZ": "Mazda",
    "JF1": "Subaru",
    "JF2": "Subaru",
}


# ------------------------------------------------------------------
# Результат разбора
# ------------------------------------------------------------------


@dataclass
class VinInfo:
    vin: str
    valid: bool = False
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    wmi: str | None = None
    vds: str | None = None  # позиции 4-8
    serial: str | None = None  # позиции 12-17
    country: str | None = None
    manufacturer: str | None = None
    year: int | None = None
    year_candidates: list = field(default_factory=list)
    plant_code: str | None = None

    # Заполняется, если сработал накопленный паттерн
    modification_id: int | None = None
    pattern_confidence: int = 0


# ------------------------------------------------------------------
# Базовые функции
# ------------------------------------------------------------------


def normalize(vin: str) -> str:
    """Убрать пробелы/дефисы, привести к верхнему регистру."""
    return "".join(vin.upper().split()).replace("-", "")


def check_digit(vin: str) -> str:
    """Вычислить контрольную цифру (позиция 9)."""
    total = sum(TRANSLIT[ch] * w for ch, w in zip(vin, WEIGHTS))
    rem = total % 11
    return "X" if rem == 10 else str(rem)


def decode_year(
    code: str, seventh_is_digit: bool | None = None, today: date | None = None
) -> tuple[int | None, list[int]]:
    """
    Модельный год из позиции 10.

    Код повторяется каждые 30 лет (A = и 1980, и 2010). Для авто рынка США
    неоднозначность снимается позицией 7: цифра -> 1980-2009, буква -> 2010+.
    Для остальных рынков возвращаем оба варианта и берём свежий как основной.
    """
    today = today or date.today()
    if code not in YEAR_CODES:
        return None, []

    idx = YEAR_CODES.index(code)
    candidates = []
    y = 1980 + idx
    while y <= today.year + 1:
        candidates.append(y)
        y += 30

    if not candidates:
        return None, []

    if seventh_is_digit is True:
        exact = [c for c in candidates if c <= 2009]
        if exact:
            return exact[-1], candidates
    elif seventh_is_digit is False:
        exact = [c for c in candidates if c >= 2010]
        if exact:
            return exact[0], candidates

    return candidates[-1], candidates


def country_of(first_char: str) -> str | None:
    for lo, hi, name in COUNTRY_RANGES:
        if lo <= first_char <= hi:
            return name
    return None


# ------------------------------------------------------------------
# Главная функция
# ------------------------------------------------------------------


def decode(raw_vin: str, wmi_lookup: dict | None = None, pattern_lookup=None) -> VinInfo:
    """
    wmi_lookup    — словарь WMI -> производитель (обычно подгружается из БД)
    pattern_lookup(wmi, vds) -> (modification_id, confidence) | None
    """
    vin = normalize(raw_vin)
    info = VinInfo(vin=vin)
    wmi_lookup = wmi_lookup or WMI_SEED

    # --- структурная проверка ---
    if len(vin) != 17:
        info.errors.append(f"Длина {len(vin)}, должно быть 17 символов")
        return info

    bad = set(vin) & FORBIDDEN
    if bad:
        info.errors.append(f"Запрещённые символы: {', '.join(sorted(bad))}")
    unknown = set(vin) - VALID_CHARS
    if unknown:
        info.errors.append(f"Недопустимые символы: {', '.join(sorted(unknown))}")
    if info.errors:
        return info

    # --- контрольная цифра ---
    # Обязательна только для Северной Америки (первый символ 1-5).
    # Европейцы и японцы её часто не соблюдают — для них это предупреждение.
    expected = check_digit(vin)
    if vin[8] != expected:
        msg = f"Контрольная цифра не сходится (ожидалась {expected}, в VIN {vin[8]})"
        if vin[0] in "12345":
            info.errors.append(msg)
            return info
        info.warnings.append(msg + " — для этого рынка это норма")

    info.valid = True

    # --- разбор по позициям ---
    info.wmi = vin[0:3]
    info.vds = vin[3:8]
    info.plant_code = vin[10]
    info.serial = vin[11:17]

    info.country = country_of(vin[0])
    info.manufacturer = wmi_lookup.get(info.wmi)
    if not info.manufacturer:
        info.warnings.append(f"WMI {info.wmi} нет в справочнике — заполните вручную")

    seventh_is_digit = vin[6].isdigit() if vin[0] in "12345" else None
    info.year, info.year_candidates = decode_year(vin[9], seventh_is_digit)
    if len(info.year_candidates) > 1:
        info.warnings.append("Год неоднозначен: " + " / ".join(map(str, info.year_candidates)))

    # --- накопленные паттерны ---
    if pattern_lookup:
        hit = pattern_lookup(info.wmi, info.vds)
        if hit:
            info.modification_id, info.pattern_confidence = hit
        else:
            info.warnings.append("Модификация неизвестна — выберите вручную, паттерн запомнится")

    return info


# ------------------------------------------------------------------
# Пример использования
# ------------------------------------------------------------------

if __name__ == "__main__":
    for sample in ["XTA210740X1234567", "WBA3R11040KS91420", "JTDBR32E060098765"]:
        r = decode(sample)
        print(f"\n{sample}")
        print(f"  валиден:      {r.valid}")
        print(f"  страна:       {r.country}")
        print(f"  изготовитель: {r.manufacturer}")
        print(f"  год:          {r.year}")
        print(f"  VDS:          {r.vds}")
        for w in r.warnings:
            print(f"  ! {w}")
        for e in r.errors:
            print(f"  ✗ {e}")
