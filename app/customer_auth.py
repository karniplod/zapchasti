"""Вход покупателя.

Отдельно от сотрудников намеренно: это разные люди с разными правами,
и общая таблица только и ждала бы, пока кто-нибудь перепутает проверку
роли и пустит покупателя в бэкенд. Своя кука, своя соль подписи, своя
таблица — перепутать нечего.

Логин — телефон или email: покупатель помнит их, а придуманный логин
забудет к следующей покупке. Подтверждения по SMS и письмом нет —
провайдеры не подключены, поэтому оба проверяются только на форму записи.

Быстрый вход (Google, VK, MAX, Telegram) — строка в customer_identities:
учётка у провайдера указывает на покупателя. Телефона у такого
покупателя может не быть — его спросят при оформлении заказа.
"""


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

# Ссылка из письма: в токене покупатель и сам адрес — сменил email
# после отправки, старая ссылка новый адрес не подтвердит
verify_signer = URLSafeTimedSerializer(settings.secret_key, salt="razbor-email-verify")
VERIFY_TTL_HOURS = 24
TTL_DAYS = 90  # покупатель заходит раз в полгода, гонять его за паролем незачем


# Правила телефона, email и имени — в app/validation/people.py; здесь
# ими пользуются регистрация и вход
from .validation.people import normalize_email, normalize_phone  # noqa: E402


def verify_token(customer_id: int, email: str) -> str:
    return verify_signer.dumps({"cid": customer_id, "e": email})


def read_verify_token(token: str) -> dict | None:
    """{"cid", "e"} — или None, если подпись чужая или ссылка старше суток."""
    try:
        return verify_signer.loads(token, max_age=VERIFY_TTL_HOURS * 3600)
    except (BadSignature, SignatureExpired):
        return None


BLOCKED = "Вход в кабинет закрыт. Если это ошибка — позвоните или напишите нам"


def issue(response: Response, customer_id: int, login_id: int | None = None,
          version: int = 0) -> None:
    """Кука входа. lid — строка журнала входов: по ней окно кабинета
    отличает этот вход от прошлого; v — номер сеанса покупателя:
    увеличили (выйти на всех устройствах, блокировка) — кука не подходит."""
    response.set_cookie(
        COOKIE,
        signer.dumps({"cid": customer_id, "lid": login_id, "v": version}),
        max_age=TTL_DAYS * 86400,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        path="/",
    )


async def login(session: AsyncSession, request: Request, response: Response,
                customer_id: int, method: str) -> None:
    """Вход состоялся: запись в журнал (когда, с какого устройства) и кука.
    Заблокированного не впускаем — 403 с понятным текстом."""
    row = (await session.execute(text(
        "SELECT is_blocked, session_version FROM customers WHERE id = :id"),
        {"id": customer_id})).first()
    if not row or row.is_blocked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, BLOCKED)
    lid = (await session.execute(text("""
        INSERT INTO customer_logins (customer_id, method, user_agent)
        VALUES (:c, :m, :ua) RETURNING id"""),
        {"c": customer_id, "m": method,
         "ua": (request.headers.get("user-agent") or "")[:400] or None})).scalar_one()
    # Журнал — для «последнего входа», не архив: хватит двадцати
    await session.execute(text("""
        DELETE FROM customer_logins WHERE customer_id = :c AND id NOT IN (
            SELECT id FROM customer_logins WHERE customer_id = :c ORDER BY at DESC, id DESC LIMIT 20)"""),
        {"c": customer_id})
    await session.execute(text("UPDATE customers SET last_login_at = now() WHERE id = :id"),
                          {"id": customer_id})
    await session.commit()
    issue(response, customer_id, lid, row.session_version)


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
        SELECT id, phone, name, email, is_blocked, session_version FROM customers WHERE id = :id
    """),
            {"id": data["cid"]},
        )
    ).first()
    # Заблокирован или вышел на всех устройствах — эта кука больше не вход
    if not row or row.is_blocked or row.session_version != data.get("v", 0):
        return None
    return {"id": row.id, "phone": row.phone, "name": row.name, "email": row.email,
            "login_id": data.get("lid")}


async def current_customer(customer: dict | None = Depends(optional_customer)) -> dict:
    """Для страниц кабинета: без входа туда нечего показывать."""
    if not customer:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Войдите в кабинет")
    return customer


async def register(
    session: AsyncSession,
    phone: str | None,
    password: str,
    name: str | None,
    email: str | None,
) -> dict:
    """Регистрация по телефону или по email.

    По телефону строка могла появиться раньше — менеджер завёл её,
    оформляя заказ по звонку. Тогда человек не регистрируется заново,
    а задаёт пароль к тому, что уже есть, и сразу видит прошлые покупки.

    По email так нельзя: строку без пароля с этим email мог создать вход
    через соцсеть, и задать к ней пароль значило бы отдать кабинет любому,
    кто знает адрес.
    """
    if not phone and not email:
        raise HTTPException(422, "Укажите телефон или email")

    row = None
    if phone:
        row = (await session.execute(
            text("SELECT id, password_hash FROM customers WHERE phone = :p"),
            {"p": phone})).first()
        if row and row.password_hash:
            raise HTTPException(409, "Этот телефон уже зарегистрирован — войдите")

    if email:
        taken = (await session.execute(text("""
            SELECT c.id, c.email_verified_at, c.phone,
                   EXISTS (SELECT 1 FROM customer_identities i WHERE i.customer_id = c.id)
                   OR EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id) AS used
              FROM customers c WHERE lower(c.email) = :e"""),
            {"e": email})).first()
        # Адрес зарегистрировали, но так и не подтвердили — это мог быть
        # кто угодно, вписавший чужую почту. Настоящий владелец заводит
        # кабинет заново: войти без письма всё равно нельзя
        if taken and not taken.email_verified_at and not taken.phone and not taken.used \
                and not row:
            row = taken
        elif taken and (not row or taken.id != row.id):
            raise HTTPException(409, "Этот email уже зарегистрирован — войдите")

    params = {
        "p": phone,
        "h": hash_password(password),
        "n": (name or "").strip() or None,
        "e": email,
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
    return {"id": cid}


async def authenticate(session: AsyncSession, kind: str, login: str,
                       password: str) -> dict | None:
    """kind — phone или email, как вернул parse_login."""
    where = "phone = :l" if kind == "phone" else "lower(email) = :l"
    row = (
        await session.execute(
            text(f"""SELECT id, phone, email, email_verified_at, password_hash
                       FROM customers WHERE {where}"""),
            {"l": login},
        )
    ).first()

    # Хеш проверяем всегда: иначе по времени ответа видно, какие
    # телефоны у нас есть
    stored = (row.password_hash if row else None) or "$2b$12$" + "x" * 53
    ok = verify_password(password, stored)

    if not row or not row.password_hash or not ok:
        return None

    result = {"id": row.id, "phone": row.phone, "email": row.email,
              "email_verified": row.email_verified_at is not None}
    if kind == "email" and not result["email_verified"]:
        return result      # вход ещё не состоялся — время входа не трогаем
    await session.execute(
        text("UPDATE customers SET last_login_at = now() WHERE id = :id"), {"id": row.id}
    )
    await session.commit()
    return result


async def identity_login(
    session: AsyncSession,
    provider: str,
    subject: str,
    *,
    name: str | None = None,
    email: str | None = None,
    email_verified: bool = False,
    phone: str | None = None,
    display: str | None = None,
) -> int:
    """Вход через провайдера: находит покупателя или заводит нового.

    Учётка уже привязана — входим в неё. Нет — ищем покупателя по email,
    но только подтверждённому провайдером: иначе любой, кто заведёт у
    провайдера чужой адрес, вошёл бы в чужой кабинет. По телефону не
    ищем вовсе — провайдер его не подтверждает. Не нашли — новый кабинет.
    """
    cid = (await session.execute(text("""
        SELECT customer_id FROM customer_identities
         WHERE provider = :pr AND subject = :s"""),
        {"pr": provider, "s": subject})).scalar()

    email = normalize_email(email) if email else None
    phone = normalize_phone(phone) if phone else None

    if cid is None and email and email_verified:
        cid = (await session.execute(
            text("SELECT id FROM customers WHERE lower(email) = :e"),
            {"e": email})).scalar()

    if cid is None:
        # Телефон и email кладём, только если они ещё ничьи: уникальность
        # нарушать нельзя, а чужой номер в своём кабинете — путаница
        if phone and (await session.execute(
                text("SELECT 1 FROM customers WHERE phone = :p"), {"p": phone})).first():
            phone = None
        if email and (await session.execute(
                text("SELECT 1 FROM customers WHERE lower(email) = :e"),
                {"e": email})).first():
            email = None
        cid = (await session.execute(text("""
            INSERT INTO customers (phone, email, name) VALUES (:p, :e, :n)
            RETURNING id"""), {"p": phone, "e": email, "n": (name or "").strip() or None})
        ).scalar_one()

    await session.execute(text("""
        INSERT INTO customer_identities (provider, subject, customer_id, display)
        VALUES (:pr, :s, :c, :d)
        ON CONFLICT (provider, subject) DO UPDATE SET display = EXCLUDED.display"""),
        {"pr": provider, "s": subject, "c": cid, "d": display or name})
    await session.execute(
        text("UPDATE customers SET last_login_at = now() WHERE id = :id"), {"id": cid})
    # Провайдер подтвердил почту — письмо от нас уже не нужно
    if email and email_verified:
        await session.execute(text("""
            UPDATE customers SET email_verified_at = now()
             WHERE id = :id AND lower(email) = :e AND email_verified_at IS NULL"""),
            {"id": cid, "e": email})
    await session.commit()
    return cid
