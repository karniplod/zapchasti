"""Определение города покупателя по IP.

Нужно ровно для одного: подставить город в фильтре каталога, чтобы
человек сразу видел, что есть рядом с ним. Это подсказка, а не
ограничение — выбранный вручную город всегда важнее.

База берётся локально (GeoLite2-City.mmdb), а не через внешний сервис:
запрос на сторону добавлял бы задержку к каждой первой загрузке,
зависел бы от чужой доступности и отправлял бы туда IP посетителей.

Файла может не быть — тогда определение просто выключено, и каталог
работает как раньше. Это не ошибка: на машине разработчика и в свежей
установке базы нет.
"""

import ipaddress
import logging
from pathlib import Path

log = logging.getLogger("razbor.geo")

_reader = None
_tried = False


def _load(db_path: Path):
    """Читатель базы открывается один раз и живёт до перезапуска:
    файл на 70 МБ, открывать его на каждый запрос незачем."""
    global _reader, _tried
    if _tried:
        return _reader
    _tried = True

    if not db_path.exists():
        log.info("GeoIP отключён: нет файла %s", db_path)
        return None
    try:
        import geoip2.database

        _reader = geoip2.database.Reader(str(db_path))
        log.info("GeoIP включён: %s", db_path)
    except ImportError:
        log.info("GeoIP отключён: не установлен geoip2")
    except Exception as e:  # битый или неполный файл
        log.warning("GeoIP отключён: %s", e)
    return _reader


def client_ip(request) -> str | None:
    """Настоящий адрес посетителя.

    За обратным прокси request.client.host — это сам прокси, поэтому
    смотрим X-Forwarded-For. Заголовок подделывается кем угодно, но
    цена ошибки здесь — неверно подставленный город в фильтре, так что
    отдельная настройка доверенных прокси того не стоит.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Первый адрес в цепочке — исходный клиент
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def detect_city(request, db_path: Path) -> str | None:
    """Русское название города или None, если определить не удалось."""
    ip = client_ip(request)
    if not ip:
        return None

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None

    # Локальная разработка и внутренняя сеть: города у таких адресов нет,
    # и в базу за ним ходить незачем
    if addr.is_private or addr.is_loopback or addr.is_reserved:
        return None

    reader = _load(db_path)
    if reader is None:
        return None

    try:
        city = reader.city(ip).city
        # Русское название, если оно есть в базе: сравнивать с филиалами
        # надо на одном языке
        return city.names.get("ru") or city.name
    except Exception:
        # Адреса нет в базе — обычное дело, не ошибка
        return None
