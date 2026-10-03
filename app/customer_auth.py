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

# Ссылка из письма: в токене покупатель и сам адрес — сменил email
# после отправки, старая ссылка новый адрес не подтвердит
verify_signer = URLSafeTimedSerializer(settings.secret_key, salt="razbor-email-verify")
VERIFY_TTL_HOURS = 24
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


# Простая проверка формы: «что-то@что-то.зона». Полная по RFC пропускает
# такое, что ни один почтовик не примет, а настоящая проверка — письмо
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


# Имя человека: с буквы, дальше буквы, пробел, дефис, апостроф, точка.
# Цифры и подчёркивание \w тоже пропустил бы — их вычитаем отдельно
NAME_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ .'’-]){0,79}$")


# ФИО получателя заказа: каждое слово — буквы, внутри дефис или апостроф
# (Иванова-Петрова, д’Артаньян). Частицы отчества пишутся со строчной
FIO_WORD_RE = re.compile(r"^[^\W\d_]+(?:[-'’][^\W\d_]+)*$")
FIO_LOWER = {"оглы", "кызы", "улы", "гызы", "уулу"}


def check_fio(raw: str, no_patronymic: bool = False) -> tuple[str | None, str | None]:
    """ФИО получателя заказа → (ФИО с заглавных букв, текст ошибки).
    Нужны фамилия, имя и отчество; «нет отчества» — фамилия и имя.
    Без инициалов: по «Иванов И. И.» посылку в пункте выдачи не отдадут.
    Те же правила — в static/js/validate.js (Check.fio)."""
    words = (raw or "").split()
    need = "фамилию и имя" if no_patronymic else "фамилию, имя и отчество"
    if not words:
        return None, f"Укажите {need}"
    if len(" ".join(words)) > 80:
        return None, "Слишком длинно — до 80 символов"
    for w in words:
        if "." in w or len(w.strip("-'’")) < 2:
            return None, "Полностью, без инициалов: Иванов Иван Иванович"
        if not FIO_WORD_RE.match(w):
            return None, "Только буквы, пробел и дефис"
    if len(words) > 5:
        return None, "Фамилия, имя и отчество — не больше пяти слов"
    if len(words) < (2 if no_patronymic else 3):
        if len(words) == 2:
            return None, "Добавьте отчество — или отметьте «Нет отчества»"
        return None, f"Нужны {need.replace('фамилию', 'фамилия')} — через пробел"
    fixed = [w if w.lower() in FIO_LOWER else
             "-".join(p[:1].upper() + p[1:] for p in w.split("-")) for w in words]
    return " ".join(fixed), None


def normalize_email(raw: str) -> str | None:
    v = (raw or "").strip().lower()
    return v if len(v) <= 200 and EMAIL_RE.match(v) else None


def parse_login(raw: str) -> tuple[str, str] | None:
    """Что ввели в поле «Телефон или email»: есть @ — почта, иначе телефон."""
    if "@" in (raw or ""):
        v = normalize_email(raw)
        return ("email", v) if v else None
    v = normalize_phone(raw)
    return ("phone", v) if v else None


def verify_token(customer_id: int, email: str) -> str:
    return verify_signer.dumps({"cid": customer_id, "e": email})


def read_verify_token(token: str) -> dict | None:
    """{"cid", "e"} — или None, если подпись чужая или ссылка старше суток."""
    try:
        return verify_signer.loads(token, max_age=VERIFY_TTL_HOURS * 3600)
    except (BadSignature, SignatureExpired):
        return None


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
