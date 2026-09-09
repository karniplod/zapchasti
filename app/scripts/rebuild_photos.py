"""Пережать снимки, залитые до того, как загрузка стала это делать сама.

Такие строки видно по пустому thumb: файл лежит оригиналом с телефона,
на несколько мегабайт, и именно его тянет превью в списках машин.

    python -m app.scripts.rebuild_photos            # показать, что будет
    python -m app.scripts.rebuild_photos --apply    # пережать

Почему не reprocess_existing() из services/images.py: та функция ходит
по диску и ничего не знает про базу — она сохраняет файл под новым
именем и удаляет старый, а в donor_photos остаётся путь к удалённому.
Здесь порядок обратный: сначала запись в базе, коммит, и только потом
удаление оригинала. Прервётся на середине — потеряем место на диске,
но не снимок.
"""

import argparse
import asyncio

from sqlalchemy import text

from ..config import settings
from ..database import dispose, get_session
from ..services.images import process

TABLES = [("donor_photos", "donor_id"), ("part_photos", "part_id")]


async def rebuild(apply: bool) -> None:
    agen = get_session()
    session = await agen.__anext__()

    for table, owner in TABLES:
        rows = (
            await session.execute(
                text(f"""
            SELECT id, {owner} AS owner, path
              FROM {table}
             WHERE thumb IS NULL
             ORDER BY id
        """)
            )
        ).all()

        print(f"\n{table}: без миниатюры {len(rows)}")

        for row in rows:
            # В базе путь публичный (/media/...), на диске — от media_root
            file = settings.media_root / row.path.removeprefix("/media/")
            if not file.exists():
                print(f"   {row.id:4} ФАЙЛА НЕТ: {row.path}")
                continue

            before = file.stat().st_size
            if not apply:
                print(f"   {row.id:4} {row.path}  {before // 1024} КБ")
                continue

            img = process(file.read_bytes(), file.parent)
            await session.execute(
                text(f"""
                UPDATE {table}
                   SET path = :path, thumb = :thumb, width = :w, height = :h
                 WHERE id = :id
            """),
                {"path": img.path, "thumb": img.thumb,
                 "w": img.width, "h": img.height, "id": row.id},
            )
            await session.commit()

            file.unlink()
            print(f"   {row.id:4} {before // 1024} КБ -> {img.bytes // 1024} КБ  {img.path}")

    await agen.aclose()
    await dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="без него только показывает")
    args = ap.parse_args()
    asyncio.run(rebuild(args.apply))
    if not args.apply:
        print("\nЭто просмотр. Пережать: python -m app.scripts.rebuild_photos --apply")
