"""Общая обвязка для внешних источников каталожных номеров.

Правила, одинаковые для всех парсеров, собраны здесь, чтобы каждый новый
источник не изобретал их заново:

  • один и тот же вопрос задаём один раз — ответ живёт в кеше, включая
    отрицательный: «ничего не нашлось» тоже результат;
  • между запросами к одному источнику держим паузу — чужой сервер нам
    ничего не должен;
  • жёсткий таймаут: приёмка детали не может ждать сеть;
  • источник, который упал или заблокировал нас, не должен ронять форму,
    поэтому любая ошибка гасится и превращается в «кандидатов нет»;
  • все внешние источники выключены по умолчанию. Включаются настройкой
    PARSERS_ENABLED — чтобы поднятый где-то стенд не начал ходить наружу
    сам по себе.

Сеть трогаем в пуле потоков: urllib синхронный, и без этого он подвесит
весь сервер на время запроса.
"""

import gzip
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ...config import settings

log = logging.getLogger("razbor.parsers")

TIMEOUT = 12
CACHE_DAYS = 30
CACHE_EMPTY_DAYS = 2

# Представляемся честно: это не попытка выдать себя за браузер человека,
# а обычный клиент, по которому видно, кто пришёл.
# Только латиница: HTTP-заголовки не бывают в UTF-8, кириллица здесь
# роняет запрос ещё до отправки
UA = "Mozilla/5.0 (compatible; AvtodonorBot/1.0; parts catalog lookup)"

# Между запросами к одному источнику — пауза. Считается в памяти
# процесса, этого хватает: запросов тут единицы в час
_last_call: dict[str, float] = {}
MIN_INTERVAL = 3.0


@dataclass
class Found:
    """Номер, найденный источником, и чем он подкреплён."""

    code: str
    weight: float
    note: str


def enabled() -> bool:
    return bool(getattr(settings, "parsers_enabled", False))


def fetch_html(url: str, source: str) -> str | None:
    """Синхронный GET. None означает «не смогли» — это не то же самое,
    что «ничего не нашли», и в кеш такое попадать не должно."""
    wait = MIN_INTERVAL - (time.monotonic() - _last_call.get(source, 0))
    if wait > 0:
        time.sleep(wait)
    _last_call[source] = time.monotonic()

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
            if r.headers.get("content-encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # 429 и 403 — нас попросили не ходить. Это не ошибка кода
        log.info("%s ответил %s", source, e.code)
    except Exception as e:
        log.info("%s недоступен: %s", source, type(e).__name__)
    return None


def fetch_json(url: str, source: str) -> tuple[dict | None, int | None]:
    """GET с разбором JSON. Возвращает (данные, код ответа).

    При ошибке возвращается разобранное тело ответа, а не None:
    поставщик объясняет причину словами («This project does not have
    the access to Custom Search JSON API»), и пересказывать её своими
    догадками — значит гонять человека по кругу.
    """
    wait = MIN_INTERVAL - (time.monotonic() - _last_call.get(source, 0))
    if wait > 0:
        time.sleep(wait)
    _last_call[source] = time.monotonic()

    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace")), r.status
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body = None
        message = ((body or {}).get("error") or {}).get("message")
        log.info("%s ответил %s: %s", source, e.code, message or "без пояснения")
        return body, e.code
    except Exception as e:
        log.info("%s недоступен: %s", source, type(e).__name__)
        return None, None


async def spent_today(session: AsyncSession, source: str) -> int:
    """Сколько раз сегодня реально ходили наружу.

    Считаем по кешу: строка появляется только после живого запроса,
    ответы из кеша сюда не попадают. Повторный запрос той же строки
    после протухания кеша обновляет строку, а не добавляет, — значит
    счёт слегка занижен. Для потолка это в безопасную сторону.
    """
    return (
        await session.execute(
            text("""
        SELECT count(*) FROM external_lookups
         WHERE source = :s AND created_at >= date_trunc('day', now())
    """),
            {"s": source},
        )
    ).scalar_one()


async def cached(session: AsyncSession, source: str, query: str, worker) -> list[dict]:
    """Ответ из кеша или свежий.

    worker возвращает пару (items, ok). ok=False означает, что источник
    не ответил или придушил нас, — такой результат не кешируется.
    Иначе одна неудачная минута закрыла бы запрос на месяц: пустой
    ответ от заблокированного источника выглядит как честное «ничего
    не нашлось», а это разные вещи.
    """
    row = (
        await session.execute(
            text("""
        SELECT payload FROM external_lookups
         WHERE source = :s AND query = :q
           -- Тип параметра тут не выводится сам: внутри CASE он уходит
           -- текстом, и make_interval такой перегрузки не знает
           AND created_at > now() - make_interval(days => CASE
                   WHEN found > 0 THEN CAST(:days AS int)
                   ELSE CAST(:days_empty AS int) END)
    """),
            {"s": source, "q": query, "days": CACHE_DAYS, "days_empty": CACHE_EMPTY_DAYS},
        )
    ).first()
    if row is not None:
        return row.payload or []

    # Потолок проверяем перед запросом, а не после: деньги тратит
    # именно поход наружу
    limit = getattr(settings, "search_daily_limit", 0)
    if limit and await spent_today(session, source) >= limit:
        log.warning("%s: дневной потолок %s запросов исчерпан", source, limit)
        return []

    items, ok = await run_in_threadpool(worker)
    items = items or []
    if not ok:
        return []

    # Отрицательный ответ живёт меньше: источник мог просто не знать
    # этой машины сегодня, а завтра появится
    await session.execute(
        text("""
        INSERT INTO external_lookups (source, query, payload, found)
        VALUES (:s, :q, CAST(:p AS jsonb), :n)
        ON CONFLICT (source, query) DO UPDATE
           SET payload = EXCLUDED.payload,
               found = EXCLUDED.found,
               created_at = now()
    """),
        {"s": source, "q": query, "p": json.dumps(items, ensure_ascii=False), "n": len(items)},
    )
    await session.commit()
    return items


# Похоже на каталожный номер: не короче восьми знаков, только цифры
# и заглавные буквы, хотя бы одна цифра обязательна. Дефисы убираем —
# один и тот же номер пишут и с ними, и без
NUMBER_TOKEN = re.compile(r"\b(?=[0-9A-Z-]{8,17}\b)(?=[^ ]*\d)[0-9A-Z][0-9A-Z-]{6,16}\b")


def extract_numbers(fragments: list[str]) -> dict[str, int]:
    """Номера-кандидаты из кусков текста и сколько раз каждый встретился."""
    counts: dict[str, int] = {}
    for fragment in fragments:
        for token in NUMBER_TOKEN.findall(fragment.upper()):
            code = token.replace("-", "")
            counts[code] = counts.get(code, 0) + 1
    return counts
