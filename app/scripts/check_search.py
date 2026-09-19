"""Проверка поискового источника каталожных номеров.

    python -m app.scripts.check_search
    python -m app.scripts.check_search --brand "ВАЗ (LADA)" --model Vesta \\
                                       --node "Дверь передняя правая"

Показывает, что вернул поисковик и какие номера из этого вышли.
Ключ не печатается — только признак, задан он или нет: вывод этой
команды можно спокойно переслать.
"""

import argparse
import asyncio

from ..config import settings
from ..services.oem import BRAND_MASKS
from ..services.parsers import search as web
from ..services.parsers.base import extract_numbers


def mask_state(value: str) -> str:
    """Про ключ говорим только «задан» и сколько знаков — сам ключ
    в вывод попасть не должен."""
    return f"задан ({len(value)} знаков)" if value else "НЕ ЗАДАН"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="ВАЗ (LADA)")
    ap.add_argument("--model", default="Vesta")
    ap.add_argument("--node", default="Дверь передняя правая")
    ap.add_argument("--year", type=int)
    a = ap.parse_args()

    print("Настройки")
    print(f"  парсеры включены : {settings.parsers_enabled}")
    print(f"  поисковик        : {settings.search_provider}")
    print(f"  ключ             : {mask_state(settings.search_api_key)}")
    print(f"  cx / folder      : {mask_state(settings.search_engine_id)}")
    print(f"  потолок в сутки  : {settings.search_daily_limit}")

    if not settings.parsers_enabled:
        print("\nPARSERS_ENABLED=false — в сеть не пойдём. Это не ошибка,")
        print("так задумано: включите в .env, когда будете готовы.")
        return

    ctx = {"brand": a.brand, "model": a.model, "category_name": a.node, "year": a.year}
    query = web.build_query(ctx)
    print(f"\nЗапрос\n  {query}")

    ask = web.PROVIDERS.get(settings.search_provider)
    if ask is None:
        print(f"\nНеизвестный поисковик: {settings.search_provider}")
        return

    results, ok = await asyncio.to_thread(ask, query)
    if not ok:
        code = web.LAST_ERROR.get("code")
        said = web.LAST_ERROR.get("message")
        print("\nИсточник не ответил.")
        if said:
            print(f"  Google ({code}): {said}")

        if code == 403:
            print("\n  Почти всегда это значит: Custom Search API не включён")
            print("  в том проекте, которому принадлежит ключ. Открыть")
            print("  console.cloud.google.com/apis/library/customsearch.googleapis.com,")
            print("  выбрать вверху нужный проект и нажать Enable.")
        elif code == 429:
            print("\n  Дневная квота кончилась — значит настроено верно.")
        elif code is None:
            print("  Сети нет или поисковик молчит. У DuckDuckGo так")
            print("  выглядит ограничение по частоте — это не про ключ.")
        return

    print(f"\nОтвет: {len(results)} результатов")
    for r in results[:5]:
        print(f"  {' '.join(r.split())[:100]}")

    counts = extract_numbers(results)
    print(f"\nПохоже на номер: {', '.join(sorted(counts)) or 'ничего'}")

    mask = BRAND_MASKS.get(a.brand.upper())
    if mask:
        kept = [c for c in sorted(counts) if web.looks_like_brand(c, a.brand)]
        print(f"После маски {a.brand}: {', '.join(kept) or 'ничего не осталось'}")
    else:
        print(f"Маски для «{a.brand}» нет — отсеивать мусор будет нечем,")
        print("кандидаты пойдут в подсказку как есть, решать будет разборщик.")


if __name__ == "__main__":
    asyncio.run(main())
