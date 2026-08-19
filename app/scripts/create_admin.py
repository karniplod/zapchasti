"""Первый администратор.

    python -m app.scripts.create_admin
"""

import asyncio
import getpass
import sys

from ..auth import ensure_admin
from ..database import SessionFactory


async def main():
    login = input("Логин администратора: ").strip().lower()
    if not login:
        sys.exit("Логин не может быть пустым")

    password = getpass.getpass("Пароль: ")
    if len(password) < 8:
        sys.exit("Пароль короче 8 символов — так нельзя")
    if password != getpass.getpass("Повторите пароль: "):
        sys.exit("Пароли не совпали")

    async with SessionFactory() as session:
        await ensure_admin(session, login, password)

    print(f"Готово. Входите на /login как {login}")


if __name__ == "__main__":
    asyncio.run(main())
