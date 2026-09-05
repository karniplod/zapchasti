"""
Импорт модификаций и комплектаций из XML вида:

    <Catalog>
      <Make id=".." name="Toyota">
        <Model id=".." name="Camry">
          <Generation id=".." name="XV70">
            <Modification id=".." name="2.5 AT">
              <YearFrom>2017</YearFrom>
              <YearTo>2023</YearTo>
              <FuelType>Бензин</FuelType>
              <DriveType>Передний</DriveType>
              <Transmission>Автомат</Transmission>
              <Power>200</Power>
              <EngineSize>2.5</EngineSize>
              <BodyType>Седан</BodyType>
              <Doors>4</Doors>
              <Complectations>
                <Complectation name="Comfort"/>
                <Complectation name="Luxury"/>
              </Complectations>
            </Modification>
          </Generation>
        </Model>
      </Make>
    </Catalog>

Ничего не тянет из интернета — читает файл, который вы получили легально
(свой прайс, лицензированная база, экспорт из вашей учётной системы).

Марка/модель/поколение ищутся или создаются по имени (как в
import_cars_base.py); модификация и комплектации всегда привязаны
к своему поколению.

Запуск:
    python -m app.scripts.import_modifications --file catalog.xml --dry-run
    python -m app.scripts.import_modifications --file catalog.xml
"""

import argparse
import asyncio
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from sqlalchemy import text

from ..database import SessionFactory

TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)


def slugify(v: str) -> str:
    s = (v or "").strip().lower().translate(TRANSLIT)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "x"


def to_int(v) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except ValueError:
        return None


def to_num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def human_time(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Progress:
    """Прогресс по объёму прочитанного файла, а не по числу строк:
    сколько всего записей внутри — заранее неизвестно, а размер известен.

    Печатает не чаще раза в `every` секунд, иначе на большом файле сам
    вывод начинает заметно тормозить импорт."""

    def __init__(self, total_bytes: int, every: float = 2.0, enabled: bool = True):
        self.total = total_bytes
        self.every = every
        self.enabled = enabled
        self.started = time.monotonic()
        self.last_print = 0.0
        self.pos = 0
        self.records = 0
        # \r работает в живом терминале; при перенаправлении в файл нужен перенос
        self.inline = sys.stdout.isatty()

    def update(self, pos: int, records: int, force: bool = False):
        self.pos, self.records = pos, records
        now = time.monotonic()
        if not self.enabled or (not force and now - self.last_print < self.every):
            return
        self.last_print = now

        elapsed = now - self.started
        share = (self.pos / self.total) if self.total else 0
        rate = self.records / elapsed if elapsed > 0 else 0
        eta = (elapsed / share - elapsed) if share > 0.001 else 0

        line = (
            f"  {share * 100:5.1f}%  |  модификаций: {self.records:>9,}"
            f"  |  {rate:>7,.0f}/сек"
            f"  |  прошло {human_time(elapsed)}"
            f"  |  осталось ~{human_time(eta)}"
        ).replace(",", " ")

        if self.inline:
            print(f"\r{line}", end="", flush=True)
        else:
            print(line, flush=True)

    def done(self):
        if self.enabled and self.inline:
            print()


async def upsert_brand(session, name: str, cache: dict) -> int:
    if name in cache:
        return cache[name]
    row = (
        await session.execute(
            text("""
        INSERT INTO brands (name, slug, source)
        VALUES (:n, :s, 'manual')
        ON CONFLICT (slug) DO UPDATE SET name = brands.name
        RETURNING id
    """),
            {"n": name, "s": slugify(name)},
        )
    ).first()
    cache[name] = row.id
    return row.id


async def upsert_model(session, brand_id: int, name: str, cache: dict) -> int:
    key = (brand_id, name)
    if key in cache:
        return cache[key]
    row = (
        await session.execute(
            text("""
        INSERT INTO models (brand_id, name, slug, source)
        VALUES (:b, :n, :s, 'manual')
        ON CONFLICT (brand_id, slug) DO UPDATE SET name = models.name
        RETURNING id
    """),
            {"b": brand_id, "n": name, "s": slugify(name)},
        )
    ).first()
    cache[key] = row.id
    return row.id


async def upsert_generation(
    session, model_id: int, name: str, body_type, year_from, year_to, cache: dict
) -> int:
    key = (model_id, name)
    if key in cache:
        return cache[key]
    row = (
        await session.execute(
            text("""
        INSERT INTO generations (model_id, name, body_type, year_from, year_to, source)
        VALUES (:m, :n, :b, COALESCE(:yf, 1900), :yt, 'manual')
        ON CONFLICT (model_id, name) DO UPDATE
            SET body_type = COALESCE(generations.body_type, EXCLUDED.body_type),
                year_to   = COALESCE(generations.year_to, EXCLUDED.year_to)
        RETURNING id
    """),
            {"m": model_id, "n": name, "b": body_type, "yf": year_from, "yt": year_to},
        )
    ).first()
    cache[key] = row.id
    return row.id


async def upsert_modification(session, generation_id: int, mod: dict) -> tuple[int, bool]:
    """Своего ID модификации у внешних источников мы не храним, поэтому
    «та же самая» модификация определяется набором характеристик —
    под него есть уникальный индекс modifications_natural_key_idx.

    DO UPDATE, а не DO NOTHING: при конфликте RETURNING обязан вернуть id,
    иначе не к чему привязать комплектации. Заодно дозаполняем поля,
    которых в существующей строке не было."""
    row = (
        await session.execute(
            text("""
        INSERT INTO modifications
            (generation_id, engine_code, engine_volume, fuel,
             power_hp, transmission, drive, doors)
        VALUES (:g, :ec, :ev, :fu, :ph, :tr, :dr, :do)
        ON CONFLICT (generation_id, engine_code, transmission, drive, power_hp)
        DO UPDATE SET
            engine_volume = COALESCE(modifications.engine_volume, EXCLUDED.engine_volume),
            fuel          = COALESCE(modifications.fuel,          EXCLUDED.fuel),
            doors         = COALESCE(modifications.doors,         EXCLUDED.doors)
        RETURNING id, (xmax = 0) AS inserted
    """),
            {"g": generation_id, **mod},
        )
    ).first()
    return row.id, row.inserted


async def load(path: Path, dry_run: bool, batch_size: int = 1000, show_progress: bool = True):
    """batch_size — через сколько модификаций фиксировать транзакцию.
    0 (и всегда при --dry-run) — одна транзакция на весь файл.

    Порционные коммиты нужны на больших файлах: одна транзакция на
    миллион строк держит блокировки часами, раздувает WAL и при обрыве
    теряет всю работу. Скрипт идемпотентен, поэтому после обрыва его
    можно просто запустить заново — уже залитое пропустится."""
    stats = {"brands": 0, "models": 0, "generations": 0, "modifications": 0, "complectations": 0}
    brand_cache, model_cache, gen_cache = {}, {}, {}
    since_commit = 0

    total_bytes = path.stat().st_size
    progress = Progress(total_bytes, enabled=show_progress)

    async with SessionFactory() as session:
        # Файл открываем сами: по f.tell() видно, сколько уже прочитано,
        # и из этого считается процент. iterparse не грузит файл целиком.
        with path.open("rb") as fh:
            context = ET.iterparse(fh, events=("end",))
            for _, elem in context:
                if elem.tag != "Make":
                    continue

                brand_name = elem.get("name")
                if not brand_name:
                    elem.clear()
                    continue
                brand_id = await upsert_brand(session, brand_name, brand_cache)

                for model_el in elem.findall("Model"):
                    model_name = model_el.get("name")
                    if not model_name:
                        continue
                    model_id = await upsert_model(session, brand_id, model_name, model_cache)

                    for gen_el in model_el.findall("Generation"):
                        gen_name = gen_el.get("name") or "Все годы"

                        def field(tag, _gen=gen_el):
                            for mod_el in _gen.findall("Modification"):
                                node = mod_el.find(tag)
                                if node is not None:
                                    return node.text
                            return None

                        generation_id = await upsert_generation(
                            session,
                            model_id,
                            gen_name,
                            field("BodyType"),
                            to_int(field("YearFrom")),
                            to_int(field("YearTo")),
                            gen_cache,
                        )

                        for mod_el in gen_el.findall("Modification"):
                            mod_id, inserted = await upsert_modification(
                                session,
                                generation_id,
                                {
                                    "ec": mod_el.findtext("EngineCode"),
                                    "ev": to_num(mod_el.findtext("EngineSize")),
                                    "fu": mod_el.findtext("FuelType"),
                                    "ph": to_int(mod_el.findtext("Power")),
                                    "tr": mod_el.findtext("Transmission"),
                                    "dr": mod_el.findtext("DriveType"),
                                    "do": to_int(mod_el.findtext("Doors")),
                                },
                            )
                            if inserted:
                                stats["modifications"] += 1
                            since_commit += 1

                            comps = mod_el.find("Complectations")
                            if comps is not None:
                                for o, c_el in enumerate(comps.findall("Complectation")):
                                    c_name = c_el.get("name")
                                    if not c_name:
                                        continue
                                    r = (
                                        await session.execute(
                                            text("""
                                        INSERT INTO complectations (modification_id, name, sort_order)
                                        VALUES (:m, :n, :o)
                                        ON CONFLICT (modification_id, name) DO NOTHING
                                        RETURNING id
                                    """),
                                            {"m": mod_id, "n": c_name, "o": o},
                                        )
                                    ).first()
                                    if r:
                                        stats["complectations"] += 1

                elem.clear()

                # Марка целиком разобрана — удобная точка, чтобы показать
                # прогресс и при необходимости зафиксировать порцию
                progress.update(fh.tell(), stats["modifications"])
                if not dry_run and batch_size and since_commit >= batch_size:
                    await session.commit()
                    since_commit = 0

            progress.update(fh.tell(), stats["modifications"], force=True)
            progress.done()

        stats["brands"] = len(brand_cache)
        stats["models"] = len(model_cache)
        stats["generations"] = len(gen_cache)

        if dry_run:
            await session.rollback()
            print("[ПРОБНЫЙ ЗАПУСК] Изменения откачены, ничего не сохранено.")
        else:
            await session.commit()

    return stats


async def main():
    ap = argparse.ArgumentParser(description="Импорт модификаций и комплектаций из XML")
    ap.add_argument("--file", required=True, help="XML со структурой Make/Model/Generation/Modification")
    ap.add_argument("--dry-run", action="store_true", help="разобрать файл, ничего не записывая")
    ap.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="через сколько модификаций фиксировать транзакцию; 0 — одна на весь файл (по умолчанию 1000)",
    )
    ap.add_argument("--quiet", action="store_true", help="без строки прогресса")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        sys.exit(f"Файл не найден: {path}")

    size_mb = path.stat().st_size / 1024 / 1024
    print(f"Читаю {path.name} ({size_mb:,.1f} МБ)…".replace(",", " "))
    stats = await load(path, args.dry_run, batch_size=args.batch_size, show_progress=not args.quiet)

    print(f"""
Готово.
  Марок затронуто:      {stats["brands"]}
  Моделей затронуто:    {stats["models"]}
  Поколений затронуто:  {stats["generations"]}
  Новых модификаций:    {stats["modifications"]}
  Новых комплектаций:   {stats["complectations"]}
""")


if __name__ == "__main__":
    asyncio.run(main())
