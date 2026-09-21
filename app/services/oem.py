"""Подсказка каталожного номера при приёмке детали.

Ядро не знает, откуда берутся номера. Источник — функция, которая по
машине и узлу возвращает кандидатов со своим весом; ядро складывает
голоса и решает, можно ли подставить номер сам или надо показать список.

Почему не «взять большинство» напрямую
--------------------------------------
Голосование уменьшает ошибку, только когда источники ошибаются
по-разному. Вся розничная торговля запчастями в РФ тянет данные из
TecDoc и каталогов производителя, поэтому пять магазинов, сошедшихся
на номере, — это один источник, повторённый пять раз. Если у него
неверная замена, консенсус придаст ей вес 5/5.

Отсюда два правила:
  1. У источника есть family. Голоса внутри одной family не складываются,
     берётся сильнейший: пять зеркал TecDoc остаются одним голосом.
  2. Расхождение источников — это запрет на автоподстановку, а не повод
     выбрать популярное. Там, где источники спорят, чаще всего спорит
     сама реальность: комплектация, рестайлинг, рынок сборки. Большинство
     выберет номер массовой комплектации, а редкая — как раз та, что стоит
     денег.

Козырь разбора: деталь лежит перед разборщиком, и номер на ней отлит.
Любой каталог проигрывает металлу, поэтому последнее слово всегда за
человеком, а подсказка только экономит ему набор.
"""

import logging
import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("razbor.oem")


def normalize(code: str | None) -> str:
    """Номер без пробелов, дефисов и точек, в верхнем регистре.

    Один и тот же номер пишут как 1K0 615 301 A, 1K0-615-301-A
    и 1k0615301a — сравнивать их можно только в одном виде.
    """
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


@dataclass
class Candidate:
    code: str            # нормализованный номер
    source: str          # кто предложил
    family: str          # источник данных: голоса внутри family не складываются
    weight: float
    note: str            # человеку: почему этот номер предложен
    # Сколько раз это подтверждалось независимо. Для своей истории —
    # сколько живых людей заводило такую деталь; для внешних — 1
    cases: int = 1


# ------------------------------------------------------------------
# Источники
# ------------------------------------------------------------------


def own_part(ctx: dict) -> int:
    """Деталь, для которой считаем, — её собственный номер не голос.

    Разметка точности (record) спрашивает подсказку уже после того, как
    деталь записана. Без исключения своя история находила бы в базе
    саму эту деталь и «угадывала» её номер со стопроцентной точностью.
    0 — такой детали не бывает, условие ничего не отсекает."""
    return ctx.get("exclude_part_id") or 0


async def from_same_modification(session: AsyncSession, ctx: dict) -> list[Candidate]:
    """Тот же узел с такой же модификации.

    Самый надёжный из бесплатных: совпали и поколение, и двигатель
    с коробкой, а номер кто-то из ваших уже держал в руках.
    """
    if not ctx.get("modification_id") or not ctx.get("category_id"):
        return []

    rows = await session.execute(
        text("""
        SELECT p.oem_number AS code, count(*) AS n
          FROM parts p
          JOIN donors d ON d.id = p.donor_id
         WHERE p.category_id = :cat
           AND d.modification_id = :mod
           AND p.oem_number IS NOT NULL
           AND p.oem_verified
           AND p.id <> :self
         GROUP BY p.oem_number
         ORDER BY count(*) DESC
         LIMIT 5
    """),
        {"cat": ctx["category_id"], "mod": ctx["modification_id"], "self": own_part(ctx)},
    )
    return [
        Candidate(
            code=r.code,
            source="history_modification",
            family="own",
            # Повторы усиливают, но не бесконечно: десять одинаковых машин
            # не делают номер в десять раз вернее
            weight=6 + min(r.n - 1, 4),
            note=f"этот узел с такой же модификации, случаев: {r.n}",
            cases=r.n,
        )
        for r in rows
    ]


async def from_same_generation(session: AsyncSession, ctx: dict) -> list[Candidate]:
    """Тот же узел с того же поколения, без учёта двигателя.

    Слабее: у одного поколения номер узла может отличаться по
    комплектации и рестайлингу.
    """
    if not ctx.get("generation_id") or not ctx.get("category_id"):
        return []

    rows = await session.execute(
        text("""
        SELECT p.oem_number AS code, count(*) AS n
          FROM parts p
          JOIN donors d ON d.id = p.donor_id
         WHERE p.category_id = :cat
           AND d.generation_id = :gen
           AND p.oem_number IS NOT NULL
           AND p.oem_verified
           AND p.id <> :self
         GROUP BY p.oem_number
         ORDER BY count(*) DESC
         LIMIT 5
    """),
        {"cat": ctx["category_id"], "gen": ctx["generation_id"], "self": own_part(ctx)},
    )
    return [
        Candidate(
            code=r.code,
            source="history_generation",
            family="own",
            weight=4 + min(r.n - 1, 3),
            note=f"этот узел с того же поколения, случаев: {r.n}",
            cases=r.n,
        )
        for r in rows
    ]


async def from_applicability(session: AsyncSession, ctx: dict) -> list[Candidate]:
    """Номера, уже привязанные к этому поколению в справочнике
    применимости. Заполняется импортом кроссов."""
    if not ctx.get("generation_id"):
        return []

    rows = await session.execute(
        text("""
        SELECT DISTINCT oa.oem_number AS code
          FROM oem_applicability oa
         WHERE oa.generation_id = :gen
           AND (oa.modification_id IS NULL OR oa.modification_id = :mod)
         LIMIT 20
    """),
        {"gen": ctx["generation_id"], "mod": ctx.get("modification_id")},
    )
    return [
        Candidate(
            code=r.code,
            source="applicability",
            family="tecdoc",
            weight=3,
            note="есть в справочнике применимости для этой машины",
        )
        for r in rows
    ]


async def from_web(session: AsyncSession, ctx: dict) -> list[Candidate]:
    """Поиск в вебе по машине и узлу.

    Самый слабый источник: он не знает вашей комплектации и выдаёт
    номер той версии, что чаще попадается в интернете. Своим весом
    автоподстановку не даёт никогда — только подсказывает кандидата.
    """
    from .parsers import search as web

    filled = await web.context_for(session, ctx)
    return [
        Candidate(code=f.code, source="web_search", family="web",
                  weight=f.weight, note=f.note)
        for f in await web.fetch(session, filled)
    ]


async def from_ocr(session: AsyncSession, ctx: dict) -> list[Candidate]:
    """Номер, отлитый на самой детали, с её фотографии.

    Единственный источник, независимый и от TecDoc, и от нашей истории:
    он читает ту деталь, что лежит на полке. Требует установленного
    Tesseract — без него молча отключён, как и geoip.
    """
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return []

    # Распознавание подключается здесь: фото детали уже лежат в part_photos
    return []


# Свои источники — запрос к своей же базе, они бесплатны и мгновенны
LOCAL_SOURCES = [
    ("history_modification", from_same_modification),
    ("history_generation", from_same_generation),
    ("applicability", from_applicability),
    ("ocr", from_ocr),
]

# Внешние стоят денег и времени: у поисковиков запросы считаются
# и тарифицируются. К ним обращаемся, только если свои не справились
REMOTE_SOURCES = [
    ("web_search", from_web),
]

SOURCES = LOCAL_SOURCES + REMOTE_SOURCES

# Сколько голосов нужно, чтобы подставить номер без вопросов, и во
# сколько раз победитель должен оторваться от второго места
CONFIDENT_WEIGHT = 6.0
CONFIDENT_RATIO = 2.0


async def collect(session: AsyncSession, sources, ctx: dict) -> list[Candidate]:
    raw: list[Candidate] = []
    for name, fn in sources:
        try:
            raw.extend(await fn(session, ctx))
        except Exception:
            # Один сломанный источник не должен ронять приёмку детали
            log.exception("Источник подсказки %s упал", name)
    return raw


async def suggest(session: AsyncSession, **ctx) -> dict:
    """Кандидаты по убыванию уверенности плюс решение об автоподстановке.

    Сначала спрашиваем своё — это запрос к своей же базе, бесплатный
    и мгновенный. В интернет идём, только если своего не хватило:
    у поисковиков запросы тарифицируются, и платить за то, что мы уже
    знаем, незачем. Заодно форма не ждёт сеть там, где ответ был рядом.
    """
    raw = await collect(session, LOCAL_SOURCES, ctx)

    # Два независимых подтверждения из своей истории — это и есть
    # «знаем точно». Тот же порог, что и для автоподстановки
    settled = any(
        c.family == "own" and c.cases >= 2 and c.weight >= CONFIDENT_WEIGHT for c in raw
    )
    if not settled:
        raw += await collect(session, REMOTE_SOURCES, ctx)

    # Голоса одной family не складываются: зеркала одного первоисточника
    # не делают ответ вернее
    best: dict[tuple[str, str], Candidate] = {}
    for c in raw:
        code = normalize(c.code)
        if not code:
            continue
        key = (code, c.family)
        if key not in best or c.weight > best[key].weight:
            best[key] = c

    totals: dict[str, dict] = {}
    for (code, _family), c in best.items():
        item = totals.setdefault(
            code,
            {"code": code, "weight": 0.0, "sources": [], "notes": [],
             "families": [], "own_cases": 0},
        )
        item["weight"] += c.weight
        item["sources"].append(c.source)
        item["notes"].append(c.note)
        item["families"].append(c.family)
        if c.family == "own":
            item["own_cases"] = max(item["own_cases"], c.cases)

    items = sorted(totals.values(), key=lambda x: -x["weight"])

    # Выделяем номер, только если за него есть два независимых
    # подтверждения. Два — это либо две разные family, либо два случая
    # в своей истории: одну и ту же деталь дважды заводил живой человек,
    # глядя на неё. Один веб-источник не годится никогда, сколько бы раз
    # он ни повторил номер: он цитирует те же магазины, что и соседний
    confident = None
    if items:
        top = items[0]
        second = items[1]["weight"] if len(items) > 1 else 0.0
        independent = len(set(top["families"])) >= 2 or top["own_cases"] >= 2
        if (
            independent
            and top["weight"] >= CONFIDENT_WEIGHT
            and top["weight"] >= second * CONFIDENT_RATIO
        ):
            confident = top["code"]

    return {"candidates": items[:6], "confident": confident}


# ------------------------------------------------------------------
# Разметка для самокалибровки
# ------------------------------------------------------------------


async def record(session: AsyncSession, part_id: int, chosen: str | None, **ctx) -> None:
    """Запомнить, что предлагали источники и что человек выбрал.

    Считаем заново, а не принимаем список от браузера: подсказки —
    локальные запросы, это дешевле, чем доверять клиенту. Через пару
    сотен деталей отсюда видно, какой источник врёт.
    """
    result = await suggest(session, exclude_part_id=part_id, **ctx)
    if not result["candidates"]:
        return

    chosen_code = normalize(chosen)
    for item in result["candidates"]:
        await session.execute(
            text("""
            INSERT INTO part_number_candidates (part_id, code, source, weight, chosen)
            VALUES (:p, :c, :s, :w, :ch)
        """),
            {
                "p": part_id,
                "c": item["code"],
                "s": ",".join(sorted(set(item["sources"]))),
                "w": item["weight"],
                "ch": bool(chosen_code) and item["code"] == chosen_code,
            },
        )


async def accuracy(session: AsyncSession) -> list[dict]:
    """Попадание каждого источника: сколько раз предлагал и сколько раз
    оказался прав. Это и есть ответ на вопрос «нужен ли платный каталог»."""
    rows = await session.execute(
        text("""
        SELECT source,
               count(*)                                  AS offered,
               count(*) FILTER (WHERE chosen)            AS hit,
               round(100.0 * count(*) FILTER (WHERE chosen) / count(*), 1) AS pct
          FROM part_number_candidates
         GROUP BY source
         ORDER BY count(*) DESC
    """)
    )
    return [dict(r._mapping) for r in rows]


# Маска номера по марке — не ищет номер, а ловит опечатку сразу при вводе
BRAND_MASKS = {
    "VOLKSWAGEN": r"^[0-9A-Z]{3}[0-9]{6}[A-Z]{0,2}$",
    "AUDI": r"^[0-9A-Z]{3}[0-9]{6}[A-Z]{0,2}$",
    "SKODA": r"^[0-9A-Z]{3}[0-9]{6}[A-Z]{0,2}$",
    "SEAT": r"^[0-9A-Z]{3}[0-9]{6}[A-Z]{0,2}$",
    "TOYOTA": r"^[0-9]{5}[0-9A-Z]{5}[0-9A-Z]{0,2}$",
    "LEXUS": r"^[0-9]{5}[0-9A-Z]{5}[0-9A-Z]{0,2}$",
    "BMW": r"^[0-9]{11}$",
    "MERCEDES-BENZ": r"^A?[0-9]{10}[0-9A-Z]{0,2}$",
    # У АвтоВАЗа сосуществуют два поколения нумерации: современное
    # десятизначное с префиксом 84xx (845…, 846…, 848…) и старое
    # 21080-6201014-00, которое после нормализации превращается
    # в длинную цифровую строку.
    # Сузив маску до одного префикса 845, я выбрасывал настоящие номера:
    # живой прогон вернул 8460013091, и он отсеивался как мусор
    "ВАЗ (LADA)": r"^(84[0-9]{8}|[0-9]{11,15})$",
    "LADA": r"^(84[0-9]{8}|[0-9]{11,15})$",
}


def looks_wrong(brand: str | None, code: str | None) -> str | None:
    """Пояснение, если номер не похож на номер этой марки, иначе None.

    Это подсказка, а не запрет: маски приблизительны, а у производителя
    всегда найдётся исключение. Останавливать приёмку из-за формата
    нельзя — деталь лежит на полке, её надо принять.
    """
    code = normalize(code)
    if not brand or not code:
        return None
    mask = BRAND_MASKS.get(brand.upper())
    if not mask or re.match(mask, code):
        return None
    return f"Не похоже на номер {brand}. Проверьте, но принять можно."
