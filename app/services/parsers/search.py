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

Поисковик выбирается настройкой SEARCH_PROVIDER:

  google      — Custom Search JSON API. Отдаёт разметку JSON, не банит
                за частоту, пока есть квота, и не ломается при смене
                вёрстки. Нужен ключ и cx поискового движка.
  duckduckgo  — без ключа, но глушит по IP после десятка запросов подряд
                (отвечает 202 с пустой страницей). Годится посмотреть,
                не годится для работы на потоке.

Разбор номеров, маски и правило двух независимых источников общие:
меняется только способ получить выдачу.
"""

import logging
import re
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ..oem import BRAND_MASKS, normalize
from .base import Found, cached, enabled, extract_numbers, fetch_html, fetch_json

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
    """Заголовки и описания результатов DuckDuckGo: номера лежат в них."""
    raw = re.findall(r'class="result__(?:a|snippet)"[^>]*>(.*?)</a>', html, re.S)
    return [re.sub(r"<[^>]+>", " ", r) for r in raw]


# ------------------------------------------------------------------
# Поисковики
# ------------------------------------------------------------------
# Каждый возвращает (куски текста, ответил ли). Второе — не то же самое,
# что «ничего не нашлось»: отказ не должен попасть в кеш


def ask_google(query: str) -> tuple[list[str], bool]:
    """Custom Search JSON API.

    Квота считается по дням; когда она кончилась, приходит 429 —
    это не «номера нет», а «спросите завтра», и кешировать это нельзя.
    """
    if not settings.search_api_key or not settings.search_engine_id:
        log.warning(
            "SEARCH_PROVIDER=google, но ключ или cx не заданы — поиск отключён"
        )
        return [], False

    url = (
        "https://www.googleapis.com/customsearch/v1"
        f"?key={quote_plus(settings.search_api_key)}"
        f"&cx={quote_plus(settings.search_engine_id)}"
        f"&q={quote_plus(query)}"
        # Русская выдача: номера ищем на наших магазинах, а не на eBay
        "&hl=ru&lr=lang_ru&num=10"
    )
    data, code = fetch_json(url, SOURCE)

    if data is None:
        if code == 429:
            log.warning("Google: дневная квота исчерпана")
        elif code == 403:
            log.warning("Google: ключ отклонён — проверьте ключ и включён ли Custom Search API")
        return [], False

    items = data.get("items") or []
    # Пустой ответ от Google — честный: он отвечает 200 и говорит,
    # что ничего не нашёл. Такое кешировать можно
    return [f"{i.get('title', '')} {i.get('snippet', '')}" for i in items], True


def ask_duckduckgo(query: str) -> tuple[list[str], bool]:
    html = fetch_html("https://html.duckduckgo.com/html/?q=" + quote_plus(query), SOURCE)
    if not html:
        return [], False

    results = parse_results(html)
    if not results:
        # Пустая страница без единого результата — это не «ничего
        # не нашлось», а мягкий отказ: поисковик отвечает 202
        # и пустотой, когда считает, что мы частим
        log.info("%s: пустая выдача, похоже на ограничение частоты", SOURCE)
        return [], False
    return results, True


PROVIDERS = {"google": ask_google, "duckduckgo": ask_duckduckgo}


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

    ask = PROVIDERS.get(settings.search_provider)
    if ask is None:
        log.warning("Неизвестный SEARCH_PROVIDER=%s", settings.search_provider)
        return []

    def work():
        results, ok = ask(query)
        if not ok:
            return [], False
        counts = extract_numbers(results)
        return [{"code": c, "hits": n} for c, n in counts.items()], True

    # Поисковик входит в ключ кеша: сменили провайдера — прежние ответы
    # не выдаём за новые
    items = await cached(session, f"{SOURCE}:{settings.search_provider}", query, work)

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
