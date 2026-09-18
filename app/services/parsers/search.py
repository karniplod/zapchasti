"""Поиск номера в вебе по машине и узлу.

Что выяснилось на разведке и почему код такой:

  • Голый номер поисковик считает телефоном: запрос «8450039385» даёт
    сайты определения номеров телефона. Поэтому спрашиваем не номер,
    а машину и узел — «Лада Веста дверь передняя правая каталожный
    номер», — и вылавливаем номера из ответа.

  • В выдаче много мусора, похожего на номер: «20152023» — это диапазон
    годов, «21213610403086» — номер, но от Нивы. Первый отсекает маска
    номера по марке, второй — нет. Поэтому вес у веба низкий, и сам по
    себе он никогда не даёт автоподстановку.

  • Один поисковик — одна family. Сколько бы он ни повторил номер, это
    один голос: ответ он собирает из тех же магазинов, что и сосед.

Источник — DuckDuckGo: его html-выдача отвечает без ключа и без капчи,
в отличие от Яндекса и Google, которые на программный запрос сразу
показывают проверку. Если понадобится стабильность — тот же код
переключается на Яндекс XML или Google CSE, когда появится ключ.
"""

import logging
import re
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..oem import BRAND_MASKS, normalize
from .base import Found, cached, enabled, extract_numbers, fetch_html

log = logging.getLogger("razbor.parsers.search")

SOURCE = "web_search"
FAMILY = "web"

# Веб — слабый источник: он не знает вашей комплектации. Его дело —
# подсказать кандидата, решает человек
BASE_WEIGHT = 1.5
MAX_WEIGHT = 3.0


def build_query(ctx: dict) -> str | None:
    """Запрос из машины и узла. Без марки и узла спрашивать нечего."""
    brand = (ctx.get("brand") or "").strip()
    node = (ctx.get("category_name") or "").strip()
    if not brand or not node:
        return None

    # «ВАЗ (LADA)» в запросе мешает: скобки дробят фразу. Берём то,
    # что внутри, — латиница ищется лучше кириллицы
    inner = re.search(r"\(([^)]+)\)", brand)
    if inner:
        brand = inner.group(1).strip()

    parts = [brand, (ctx.get("model") or "").strip()]
    if ctx.get("year"):
        parts.append(str(ctx["year"]))
    parts += [node, "каталожный номер"]
    return " ".join(p for p in parts if p)


def parse_results(html: str) -> list[str]:
    """Заголовки и описания результатов: номера лежат в них."""
    raw = re.findall(r'class="result__(?:a|snippet)"[^>]*>(.*?)</a>', html, re.S)
    return [re.sub(r"<[^>]+>", " ", r) for r in raw]


def looks_like_brand(code: str, brand: str | None) -> bool:
    """Номер, не похожий на номер этой марки, до голосования не доходит.

    Это и отсекает диапазоны годов и прочие числа из текста. Если маски
    для марки нет — пропускаем всех, тогда решает вес и человек.
    """
    if not brand:
        return True
    mask = BRAND_MASKS.get(brand.upper())
    if not mask:
        return True
    return bool(re.match(mask, code))


async def fetch(session: AsyncSession, ctx: dict) -> list[Found]:
    if not enabled():
        return []

    query = build_query(ctx)
    if not query:
        return []

    def work():
        html = fetch_html(
            "https://html.duckduckgo.com/html/?q=" + quote_plus(query), SOURCE
        )
        if not html:
            return [], False

        results = parse_results(html)
        if not results:
            # Пустая страница без единого результата — это не «ничего
            # не нашлось», а мягкий отказ: поисковик отвечает 202
            # и пустотой, когда считает, что мы частим
            log.info("%s: пустая выдача, похоже на ограничение частоты", SOURCE)
            return [], False

        counts = extract_numbers(results)
        return [{"code": c, "hits": n} for c, n in counts.items()], True

    items = await cached(session, SOURCE, query, work)

    brand = ctx.get("brand")
    out: list[Found] = []
    for item in items:
        code = normalize(item["code"])
        if not code or not looks_like_brand(code, brand):
            continue
        out.append(
            Found(
                code=code,
                # Повторы в выдаче чуть усиливают, но потолок низкий:
                # это по-прежнему один источник
                weight=min(BASE_WEIGHT + 0.5 * (item["hits"] - 1), MAX_WEIGHT),
                note=f"встретился в поиске «{query}»",
            )
        )

    # Больше пяти кандидатов человеку показывать бессмысленно
    out.sort(key=lambda f: -f.weight)
    return out[:5]


async def context_for(session: AsyncSession, ctx: dict) -> dict:
    """Дополняет контекст тем, что нужно для запроса: марка, модель, год,
    название узла. Ядро передаёт только идентификаторы."""
    filled = dict(ctx)

    if ctx.get("generation_id"):
        row = (
            await session.execute(
                text("""
            SELECT b.name AS brand, m.name AS model, g.name AS generation
              FROM generations g
              JOIN models m ON m.id = g.model_id
              JOIN brands b ON b.id = m.brand_id
             WHERE g.id = :g
        """),
                {"g": ctx["generation_id"]},
            )
        ).first()
        if row:
            filled.setdefault("brand", row.brand)
            filled.setdefault("model", row.model)

    if ctx.get("category_id"):
        row = (
            await session.execute(
                text("SELECT name FROM part_categories WHERE id = :c"),
                {"c": ctx["category_id"]},
            )
        ).first()
        if row:
            filled.setdefault("category_name", row.name)

    return filled
