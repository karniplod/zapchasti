"""Проставить узлы категориям склада.

python -m app.scripts.set_nodes
"""

import asyncio

from sqlalchemy import text

from ..database import SessionFactory
from .nodes import CATEGORY_NODES, SECTION_NODES


async def main():
    async with SessionFactory() as s:
        # Сначала по разделу — грубо, зато покрывает всё дерево
        for section, node in SECTION_NODES.items():
            if not node:
                continue
            await s.execute(
                text("""
                UPDATE part_categories c SET node = :n
                 WHERE c.parent_id = (SELECT id FROM part_categories
                                       WHERE name = :s AND parent_id IS NULL)
                    OR c.name = :s
            """),
                {"n": node, "s": section},
            )

        # Затем точечно там, где раздел ошибается
        for name, node in CATEGORY_NODES.items():
            await s.execute(
                text("UPDATE part_categories SET node = :n WHERE name = :name"),
                {"n": node, "name": name},
            )

        await s.commit()

        rows = await s.execute(
            text("""
            SELECT coalesce(node, '(нет)') AS node, count(*) AS cnt
              FROM part_categories GROUP BY node ORDER BY cnt DESC
        """)
        )
        for r in rows:
            print(f"{r.node:10} {r.cnt}")


if __name__ == "__main__":
    asyncio.run(main())
