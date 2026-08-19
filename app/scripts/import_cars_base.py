"""Импорт справочника из api.cars-base.ru.

    python -m app.scripts.import_cars_base --file cars-base.json --dry-run
    python -m app.scripts.import_cars_base --file cars-base.json --min-year 1990

Источник даёт только марки и модели. Поколений нет, поэтому на каждую
модель создаётся заглушка «Все годы» с пометкой needs_review — приёмщик
уточнит реальный кузов при первой такой машине.
"""

import argparse
import asyncio
import json
import re
from pathlib import Path

from sqlalchemy import text

from ..database import SessionFactory

TRANSLIT = str.maketrans({
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z",
    "и":"i","й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r",
    "с":"s","т":"t","у":"u","ф":"f","х":"h","ц":"c","ч":"ch","ш":"sh","щ":"sch",
    "ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya",
})

def slugify(v: str) -> str:
    s = v.strip().lower().translate(TRANSLIT)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "x"


def iter_marks(path: Path):
    """Потоковый разбор, если есть ijson. Иначе — обычный json."""
    try:
        import ijson
        with path.open("rb") as f:
            yield from ijson.items(f, "data.item")
    except ImportError:
        print("ijson не установлен — читаю файл целиком, это может занять память")
        with path.open(encoding="utf-8") as f:
            yield from json.load(f)["data"]


async def run(path: Path, min_year: int, dry_run: bool, only: str | None):
    st = {"brands": 0, "models": 0, "gens": 0, "skipped": 0}

    async with SessionFactory() as s:
        for mark in iter_marks(path):
            name = (mark.get("name") or "").strip()
            if not name:
                continue
            if only and only.lower() not in name.lower():
                continue
            if (mark.get("year_to") or 0) < min_year:
                st["skipped"] += 1
                continue

            b = (await s.execute(text("""
                INSERT INTO brands (name, slug, source)
                VALUES (:n, :sl, 'cars-base')
                ON CONFLICT (slug) DO UPDATE SET name = brands.name
                RETURNING id, (xmax = 0) AS ins
            """), {"n": name, "sl": slugify(name)})).first()
            if b.ins:
                st["brands"] += 1

            for m in mark.get("models") or []:
                mname = (m.get("name") or "").strip()
                if not mname:
                    continue
                y_to = m.get("year_to")
                if (y_to or 0) < min_year:
                    st["skipped"] += 1
                    continue

                mo = (await s.execute(text("""
                    INSERT INTO models (brand_id, name, slug, source)
                    VALUES (:b, :n, :sl, 'cars-base')
                    ON CONFLICT (brand_id, slug) DO UPDATE SET name = models.name
                    RETURNING id, (xmax = 0) AS ins
                """), {"b": b.id, "n": mname, "sl": slugify(mname)})).first()
                if mo.ins:
                    st["models"] += 1

                y_from = m.get("year_from") or mark.get("year_from") or 1900
                # year_to = текущий год у выпускаемых -> храним NULL
                to = None if (y_to or 0) >= 2026 else y_to

                g = (await s.execute(text("""
                    INSERT INTO generations (model_id, name, year_from, year_to,
                                             source, needs_review)
                    VALUES (:m, 'Все годы', :yf, :yt, 'cars-base', true)
                    ON CONFLICT (model_id, name) DO UPDATE
                        SET year_to = COALESCE(generations.year_to, EXCLUDED.year_to)
                    RETURNING (xmax = 0) AS ins
                """), {"m": mo.id, "yf": y_from, "yt": to})).first()
                if g.ins:
                    st["gens"] += 1

        if dry_run:
            await s.rollback()
            print("[ПРОБНЫЙ ЗАПУСК] Ничего не сохранено.")
        else:
            await s.commit()

    return st


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="cars-base.json")
    ap.add_argument("--min-year", type=int, default=1990,
                    help="пропускать модели, снятые с выпуска раньше этого года")
    ap.add_argument("--only", help="только марки, содержащие эту подстроку")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    p = Path(a.file)
    if not p.exists():
        raise SystemExit(f"Нет файла {p}")

    st = await run(p, a.min_year, a.dry_run, a.only)
    print(f"""
Марок:      {st['brands']}
Моделей:    {st['models']}
Поколений:  {st['gens']}
Пропущено:  {st['skipped']} (старше {a.min_year})
""")

if __name__ == "__main__":
    asyncio.run(main())
