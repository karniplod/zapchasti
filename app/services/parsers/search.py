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

  brave       — Brave Search API. Свой индекс, не перепродажа чужой
                выдачи; ключ в заголовке, ответ в JSON. Рабочий вариант.
  duckduckgo  — без ключа, но глушит по IP после десятка запросов подряд
                (отвечает 202 с пустой страницей). Годится посмотреть,
                не годится для работы на потоке.

Google Custom Search JSON API здесь нет намеренно: он закрыт для новых
клиентов (ключ получить можно, но на любой запрос приходит 403),
а 1 января 2027 выключается совсем.

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


LAST_ERROR: dict = {"code": None, "message": None}


def ask_brave(query: str) -> tuple[list[str], bool]:
    """Brave Search API.

    Ключ передаётся заголовком, а не в адресе, — он не осядет в логах
    прокси и в истории запросов. На бесплатном тарифе ограничение
    примерно запрос в секунду; наша пауза между обращениями больше,
    так что в него мы не упрёмся.
    """
    if not settings.search_api_key:
        log.warning("SEARCH_PROVIDER=brave, но ключ не задан — поиск отключён")
        return [], False

    url = (
        "https://api.search.brave.com/res/v1/web/search"
        f"?q={quote_plus(query)}&count=20"
        # Русская выдача: номера ищем на наших магазинах и форумах
        "&search_lang=ru&country=RU"
    )
    data, code = fetch_json(
        url, SOURCE, headers={"X-Subscription-Token": settings.search_api_key}
    )

    if code != 200:
        said = ((data or {}).get("error") or {}).get("detail") if isinstance(data, dict) else None
        LAST_ERROR["code"] = code
        LAST_ERROR["message"] = said or (
            "ключ не принят" if code in (401, 403)
            else "превышена частота или месячная квота" if code == 429
            else None
        )
        log.warning("Brave отказал (%s): %s", code, LAST_ERROR["message"] or "без пояснения")
        return [], False

    results = (data or {}).get("web", {}).get("results") or []
    # Пустая выдача от Brave — честная: он ответил 200 и ничего не нашёл
    return [f"{r.get('title', '')} {r.get('description', '')}" for r in results], True


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


PROVIDERS = {
    "brave": ask_brave,
    "duckduckgo": ask_duckduckgo,
}


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
