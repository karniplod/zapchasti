"""Выборочный импорт кроссов из архива TecDoc-выгрузки.

Полная база — около сотни миллионов строк. Заливать её целиком на телефон
бессмысленно: нужны кроссы только для номеров, которые реально лежат
у вас на складе.

Работает в два прохода по архиву:
  1. находит TTC_ART_ID артикулов, где встретился ваш номер
  2. собирает все номера этих артикулов

    python -m app.scripts.import_cross --zip ~/downloads/_crossbase.zip
    python -m app.scripts.import_cross --zip ... --all-files   # включая NOT_OE
"""

import argparse
import asyncio
import csv
import io
import zipfile
from pathlib import Path

from sqlalchemy import text

from ..database import SessionFactory
from .nodes import node_for_cross


def norm(code: str) -> str:
    return "".join(c for c in (code or "").upper() if c.isalnum())


def members(zf: zipfile.ZipFile, all_files: bool):
    for n in sorted(zf.namelist()):
        if not n.lower().endswith(".csv"):
            continue
        if not all_files and "NOT_OE" in n:
            continue
        yield n


def rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as raw:
        stream = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        for row in csv.DictReader(stream, delimiter=";"):
            yield row


async def targets(session, force: bool) -> set[str]:
    codes = set()
    for (c,) in await session.execute(
        text("SELECT oem_number FROM parts WHERE oem_number IS NOT NULL")
    ):
        codes.add(norm(c))
    for (c,) in await session.execute(text("SELECT code FROM part_oem")):
        codes.add(norm(c))
    codes = {c for c in codes if len(c) >= 4}

    if force:
        return codes

    # Номера, по которым уже искали, пропускаем: архив статичный,
    # повторный проход даст ровно тот же результат
    done = {r[0] for r in await session.execute(text("SELECT code FROM oem_cross_lookup"))}
    return codes - done


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument(
        "--all-files",
        action="store_true",
        help="включая NOT_OE — в тридцать раз дольше",
    )
    ap.add_argument("--extra", help="файл со списком номеров, по одному на строку")
    ap.add_argument("--force", action="store_true", help="искать даже по уже проверенным номерам")
    a = ap.parse_args()

    path = Path(a.zip).expanduser()
    if not path.exists():
        raise SystemExit(f"Нет архива {path}")

    async with SessionFactory() as session:
        want = await targets(session, a.force)

    if a.extra:
        extra = {norm(line) for line in Path(a.extra).read_text().split() if norm(line)}
        if not a.force:
            async with SessionFactory() as session:
                done = {
                    r[0] for r in await session.execute(text("SELECT code FROM oem_cross_lookup"))
                }
            extra -= done
        want |= extra

    if not want:
        print("Новых номеров нет — все кроссы уже загружены.")
        return

    print(f"Ищу кроссы для номеров: {len(want)}")
    print("Уже проверенные номера пропущены (--force чтобы искать заново)")

    zf = zipfile.ZipFile(path)
    files = list(members(zf, a.all_files))
    print(f"Файлов к просмотру: {len(files)}")

    # --- проход 1: какие артикулы нам интересны ---
    art_ids: set[int] = set()
    for i, name in enumerate(files, 1):
        seen = 0
        for r in rows(zf, name):
            seen += 1
            if norm(r["CODE_PARTS"]) in want or norm(r["mainART_CODE_PARTS"]) in want:
                try:
                    art_ids.add(int(r["TTC_ART_ID"]))
                except (TypeError, ValueError):
                    pass
        print(f"  [{i}/{len(files)}] {name}: строк {seen}, артикулов найдено {len(art_ids)}")

    if not art_ids:
        print("Совпадений нет. Возможно, ваши номера отсутствуют в этой базе.")
        return

    # --- проход 2: все номера найденных артикулов ---
    batch, total = [], 0
    async with SessionFactory() as session:
        for i, name in enumerate(files, 1):
            is_oe = "NOT_OE" not in name
            for r in rows(zf, name):
                try:
                    aid = int(r["TTC_ART_ID"])
                except (TypeError, ValueError):
                    continue
                if aid not in art_ids:
                    continue

                for code, brand in (
                    (r["CODE_PARTS"], r["BRANDS"]),
                    (r["mainART_CODE_PARTS"], r["mainART_BRANDS"]),
                ):
                    c = norm(code)
                    if len(c) < 4:
                        continue
                    nm = (r["NAME_PARTS"] or "").strip()[:120]
                    batch.append(
                        {
                            "a": aid,
                            "c": c,
                            "b": (brand or "").strip(),
                            "n": nm,
                            "oe": is_oe,
                            "nd": node_for_cross(nm),
                        }
                    )

                if len(batch) >= 2000:
                    total += await flush(session, batch)
                    batch.clear()

            print(f"  [{i}/{len(files)}] {name}: сохранено {total + len(batch)}")

        if batch:
            total += await flush(session, batch)
        await session.commit()

    async with SessionFactory() as session:
        await session.execute(
            text("""
            INSERT INTO oem_cross_lookup (code, found)
            VALUES (:c, :f)
            ON CONFLICT (code) DO UPDATE
               SET found = EXCLUDED.found, checked_at = now()
        """),
            [{"c": c, "f": len(art_ids)} for c in want],
        )
        await session.commit()

    print(f"\nГотово. Артикулов: {len(art_ids)}, строк кроссов: {total}")
    print(f"Помечено проверенными номеров: {len(want)}")


async def flush(session, batch):
    await session.execute(
        text("""
        INSERT INTO oem_cross (art_id, code, brand, name_en, is_oe, node)
        VALUES (:a, :c, :b, :n, :oe, :nd)
        ON CONFLICT DO NOTHING
    """),
        batch,
    )
    return len(batch)


if __name__ == "__main__":
    asyncio.run(main())
