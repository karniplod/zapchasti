import asyncio, getpass, sys
import bcrypt
from sqlalchemy import text
from app.database import SessionFactory

ROLES = {"1": "admin", "2": "manager", "3": "dismantler"}

async def main():
    login = input("Логин: ").strip().lower()
    if not login:
        sys.exit("Логин пустой")

    name = input("Имя (можно пропустить): ").strip() or None

    print("Роль: 1 — admin, 2 — manager, 3 — dismantler")
    role = ROLES.get(input("Выбор [1]: ").strip() or "1")
    if not role:
        sys.exit("Неизвестная роль")

    pwd = getpass.getpass("Пароль: ")
    if len(pwd) < 8:
        sys.exit("Пароль короче 8 символов")
    if pwd != getpass.getpass("Повторите: "):
        sys.exit("Пароли не совпали")

    hashed = bcrypt.hashpw(pwd.encode()[:72], bcrypt.gensalt()).decode()

    async with SessionFactory() as s:
        dup = (await s.execute(
            text("SELECT id FROM users WHERE login = :l"), {"l": login})).first()
        if dup:
            sys.exit(f"Пользователь {login} уже есть")

        await s.execute(text("""
            INSERT INTO users (login, password_hash, full_name, role)
            VALUES (:l, :p, :n, CAST(:r AS user_role))
        """), {"l": login, "p": hashed, "n": name, "r": role})
        await s.commit()

    print(f"Готово: {login} ({role}). Входите на /login")

asyncio.run(main())
