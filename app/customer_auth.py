"""Вход покупателя.

Отдельно от сотрудников намеренно: это разные люди с разными правами,
и общая таблица только и ждала бы, пока кто-нибудь перепутает проверку
роли и пустит покупателя в бэкенд. Своя кука, своя соль подписи, своя
таблица — перепутать нечего.

Телефон вместо логина: покупатель помнит свой номер, а придуманный
логин забудет к следующей покупке. Подтверждения по SMS нет — провайдер
не подключён, поэтому номер проверяется только на форму записи.
"""

import re

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import hash_password, verify_password
from .config import settings
from .database import get_session

# Соль другая, чем у сотрудников: кука из бэкенда не должна подходить
# к витрине и наоборот, даже если ключ подписи один
signer = URLSafeTimedSerializer(settings.secret_key, salt="razbor-customer")

COOKIE = "razbor_customer"
TTL_DAYS = 90  # покупатель заходит раз в полгода, гонять его за паролем незачем


def normalize_phone(raw: str) -> str | None:
    """К одному виду: +79123456789.

    Один и тот же человек напишет 8 912…, +7 912… и 7(912)… — без
    приведения в таблице окажется три покупателя с одним телефоном,
    а UNIQUE на phone этого не заметит.
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return None
    return "+" + digits


def issue(response: Response, customer_id: int) -> None:
    response.set_cookie(
        COOKIE,
        signer.dumps({"cid": customer_id}),
        max_age=TTL_DAYS * 86400,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        path="/",
    )


def drop(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


async def optional_customer(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict | None:
    """Кто смотрит страницу — или None. Витрина открыта всем, поэтому
    почти везде нужен именно необязательный вариант."""
    token = request.cookies.get(COOKIE)
    if not token:
        return None

    try:
        data = signer.loads(token, max_age=TTL_DAYS * 86400)
    except (BadSignature, SignatureExpired):
        return None

    row = (
        await session.execute(
            text("""
        SELECT id, phone, name, email FROM customers WHERE id = :id
    """),
            {"id": data["cid"]},
        )
    ).first()
    return dict(row._mapping) if row else None


async def current_customer(customer: dict | None = Depends(optional_customer)) -> dict:
    """Для страниц кабинета: без входа туда нечего показывать."""
    if not customer:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Войдите в кабинет")
    return customer


async def register(
    session: AsyncSession, phone: str, password: str, name: str | None, email: str | None
) -> dict:
    """Регистрация поверх существующей записи покупателя.

    Строка могла появиться раньше — менеджер завёл её, оформляя заказ
    по телефону. Тогда человек не регистрируется заново, а задаёт
    пароль к тому, что уже есть, и сразу видит прошлые покупки.
    """
    row = (
        await session.execute(
            text("SELECT id, password_hash FROM customers WHERE phone = :p"),
            {"p": phone},
        )
    ).first()

    if row and row.password_hash:
        raise HTTPException(409, "Этот телефон уже зарегистрирован — войдите")

    params = {
        "p": phone,
        "h": hash_password(password),
        "n": (name or "").strip() or None,
        "e": (email or "").strip() or None,
    }

    if row:
        await session.execute(
            text("""
            UPDATE customers
               SET password_hash = :h,
                   name  = coalesce(:n, name),
                   email = coalesce(:e, email)
             WHERE id = :id
        """),
            {**params, "id": row.id},
        )
        cid = row.id
    else:
        cid = (
            await session.execute(
                text("""
            INSERT INTO customers (phone, password_hash, name, email)
            VALUES (:p, :h, :n, :e)
            RETURNING id
        """),
                params,
            )
        ).scalar_one()

    await session.commit()
    return {"id": cid, "phone": phone}


async def authenticate(session: AsyncSession, phone: str, password: str) -> dict | None:
    row = (
        await session.execute(
            text("SELECT id, phone, password_hash FROM customers WHERE phone = :p"),
            {"p": phone},
        )
    ).first()

    # Хеш проверяем всегда: иначе по времени ответа видно, какие
    # телефоны у нас есть
    stored = (row.password_hash if row else None) or "$2b$12$" + "x" * 53
    ok = verify_password(password, stored)

    if not row or not row.password_hash or not ok:
        return None

    await session.execute(
        text("UPDATE customers SET last_login_at = now() WHERE id = :id"), {"id": row.id}
    )
    await session.commit()
    return {"id": row.id, "phone": row.phone}
