"""Вход в бэкенд.

Сессия — подписанная кука, без хранения в БД: сотрудников единицы,
таблица сессий тут лишняя сложность. Ключ подписи меняется -> все
разлогинены, что и нужно при утечке.

Роли:
  admin       всё
  manager     приёмка, детали, заказы, цены
  dismantler  только разбор и печать этикеток
"""

import bcrypt
from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_session

signer = URLSafeTimedSerializer(settings.secret_key, salt="razbor-session")
# Отдельная соль: куку браузера нельзя выдать за токен приложения и наоборот,
# у них разный срок жизни
app_signer = URLSafeTimedSerializer(settings.secret_key, salt="razbor-app")

ROLE_RANK = {"dismantler": 1, "manager": 2, "admin": 3}

# Что видит сотрудник по нажатию на свою роль в меню. Список сверен
# с require_role в роутерах: поменялся доступ — поправить и здесь,
# иначе подсказка начнёт обещать то, чего нет
ROLE_INFO = {
    "dismantler": {
        "label": "Разборщик",
        "can": [
            "Разбор машин: снимать детали, фотографировать",
            "Печатать этикетки с QR",
            "Дополнять справочник моделей при разборе",
            "Смотреть сводку, машины, детали и заказы",
        ],
    },
    "manager": {
        "label": "Менеджер",
        "can": [
            "Всё, что может разборщик",
            "Принимать машины и отдельные детали",
            "Править цены, статусы и карточки, удалять детали и фото",
            "Вести заказы и заявки покупателей",
            "Смотреть отчёты и проверять справочник",
        ],
    },
    "admin": {
        "label": "Администратор",
        "can": [
            "Всё, что может менеджер",
            "Полный доступ ко всем разделам бэкенда",
        ],
    },
}


# ------------------------------------------------------------------
# Пароли
# ------------------------------------------------------------------


def hash_password(raw: str) -> str:
    # bcrypt читает максимум 72 байта, остальное молча отбрасывает
    return bcrypt.hashpw(raw.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode()[:72], hashed.encode())
    except (ValueError, TypeError):
        # Битый или подставной хеш — пароль просто не подошёл
        return False


# ------------------------------------------------------------------
# Куки
# ------------------------------------------------------------------


def issue_session(response: Response, user_id: int, role: str) -> None:
    token = signer.dumps({"uid": user_id, "role": role})
    response.set_cookie(
        settings.session_cookie,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,  # JS до куки не дотянется
        secure=not settings.debug,  # только по HTTPS в проде
        samesite="lax",  # переживает переход с Авито
        path="/",
    )


def drop_session(response: Response) -> None:
    response.delete_cookie(settings.session_cookie, path="/")


def issue_app_token(user_id: int, role: str) -> str:
    """Токен для Android-приложения: приходит в заголовке Authorization."""
    return app_signer.dumps({"uid": user_id, "role": role})


def _read_token(request: Request) -> dict:
    """Bearer-токен приложения, иначе кука браузера."""
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        token, s, ttl = auth[7:].strip(), app_signer, settings.app_token_ttl_days * 86400
    else:
        token = request.cookies.get(settings.session_cookie)
        s, ttl = signer, settings.session_ttl_hours * 3600
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужно войти")

    try:
        # Время подписи — это и есть время входа: токен выдаётся ровно
        # при вводе пароля и больше не переподписывается
        data, signed_at = s.loads(token, max_age=ttl, return_timestamp=True)
        return {**data, "signed_at": signed_at}
    except SignatureExpired:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Смена закончилась, войдите заново"
        ) from None
    except BadSignature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия недействительна") from None


# ------------------------------------------------------------------
# Аутентификация
# ------------------------------------------------------------------


async def authenticate(session: AsyncSession, login: str, password: str) -> dict | None:
    row = (
        await session.execute(
            text("""
        SELECT id, login, password_hash, full_name, role, is_active
          FROM users WHERE login = :login
    """),
            {"login": login.strip().lower()},
        )
    ).first()

    # Хеш проверяем даже когда пользователя нет: иначе по времени ответа
    # можно перебрать существующие логины
    stored = row.password_hash if row else "$2b$12$" + "x" * 53
    ok = verify_password(password, stored)

    if not row or not ok or not row.is_active:
        return None

    await session.execute(
        text("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = :id"),
        {"id": row.id},
    )
    await session.commit()
    return {"id": row.id, "login": row.login, "name": row.full_name, "role": row.role}


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    data = _read_token(request)

    # Роль перечитываем из БД: понизили права — действует сразу,
    # не после истечения куки
    row = (
        await session.execute(
            text("""
        SELECT id, login, full_name, role, is_active, branch_id
          FROM users WHERE id = :id
    """),
            {"id": data["uid"]},
        )
    ).first()

    if not row or not row.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Учётная запись отключена")

    return {"id": row.id, "login": row.login, "name": row.full_name,
            "role": row.role, "branch_id": row.branch_id,
            "login_at": data["signed_at"]}


async def optional_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> dict | None:
    """Для публичных страниц: показать шапку бэкенда, если сотрудник вошёл."""
    try:
        return await current_user(request, session)
    except HTTPException:
        return None


def require_role(minimum: str):
    """require_role('manager') пропустит manager и admin, но не разборщика."""
    need = ROLE_RANK[minimum]

    async def guard(user: dict = Depends(current_user)) -> dict:
        if ROLE_RANK.get(user["role"], 0) < need:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return user

    return guard


# ------------------------------------------------------------------
# Первый администратор
# ------------------------------------------------------------------


async def ensure_admin(session: AsyncSession, login: str, password: str) -> None:
    """Вызывается скриптом при развёртывании:
    python -m app.scripts.create_admin"""
    exists = (
        await session.execute(text("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"))
    ).first()
    if exists:
        return

    await session.execute(
        text("""
        INSERT INTO users (login, password_hash, full_name, role)
        VALUES (:l, :p, 'Администратор', 'admin')
    """),
        {"l": login.lower(), "p": hash_password(password)},
    )
    await session.commit()
