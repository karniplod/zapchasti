"""Пользователи в бэкенде: покупатели и сотрудники.

Покупатели (менеджер и админ): поиск, карточка — заказы, адреса, входы
с устройствами, баллы; правка контактов, заметка для своих, блокировка
и «выйти на всех устройствах». Блокировка не трогает заказы: она
закрывает вход в кабинет и оформление.

Сотрудники (только админ): завести, сменить роль и филиал, задать
пароль, отключить. Себя отключить или понизить нельзя, и последний
действующий админ остаётся всегда — иначе бэкенд останется без хозяина.

Пароль и отключение заканчивают сеансы: у учётки растёт
session_version, кука и токен приложения с прежним номером не подходят
(app/auth.py, app/customer_auth.py). Каждая правка — в account_audit.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import loyalty, useragent
from ..auth import ROLE_INFO, current_user, hash_password, require_role
from ..database import get_session
from ..templating import templates
from ..validation import people, require
from .shop import ORDER_LABELS

router = APIRouter(tags=["users"])

PAGE_SIZE = 50
METHODS = {"password": "пароль", "register": "регистрация", "email_link": "ссылка из письма",
           "google": "Google", "vk": "VK ID", "yandex": "Яндекс ID", "telegram": "Telegram", "max": "MAX"}
LOGIN_RE = re.compile(r"^[a-z0-9._-]{3,32}$")


async def audit(session: AsyncSession, user: dict, kind: str, target_id: int, action: str,
                details: str | None = None) -> None:
    await session.execute(text("""
        INSERT INTO account_audit (actor_id, target_kind, target_id, action, details)
        VALUES (:a, :k, :t, :ac, :d)"""),
        {"a": user["id"], "k": kind, "t": target_id, "ac": action, "d": details})


async def audit_of(session: AsyncSession, kind: str, target_id: int) -> list[dict]:
    return [dict(r._mapping) for r in await session.execute(text("""
        SELECT a.at, a.action, a.details, coalesce(u.full_name, u.login) AS who
          FROM account_audit a LEFT JOIN users u ON u.id = a.actor_id
         WHERE a.target_kind = :k AND a.target_id = :t ORDER BY a.at DESC LIMIT 30"""),
        {"k": kind, "t": target_id})]


# ------------------------------------------------------------------
# Страницы
# ------------------------------------------------------------------


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, user=Depends(require_role("manager"))):
    return templates.TemplateResponse("admin/users.html", {"request": request, "user": user})


@router.get("/users/customers/{customer_id}", response_class=HTMLResponse)
async def customer_page(customer_id: int, request: Request, user=Depends(require_role("manager"))):
    return templates.TemplateResponse("admin/customer.html", {
        "request": request, "user": user, "customer_id": customer_id})


# ------------------------------------------------------------------
# Покупатели
# ------------------------------------------------------------------

CUSTOMER_SORT = {
    "new": "c.created_at DESC",
    "login": "c.last_login_at DESC NULLS LAST",
    "orders": "orders DESC, c.created_at DESC",
    "spent": "spent DESC, c.created_at DESC",
}


@router.get("/api/manage/customers")
async def customers_list(q: str = "", show: str = "", sort: str = "new", page: int = 1,
                         session: AsyncSession = Depends(get_session),
                         user=Depends(require_role("manager"))):
    """Поиск одной строкой: имя, телефон любой записью, email, номер заказа.
    show: blocked — заблокированные, buyers — с заказами, idle — без заказов."""
    q = q.strip()
    digits = re.sub(r"\D", "", q)
    conds, params = [], {}
    if q:
        conds.append("""(c.name ILIKE :q OR c.email ILIKE :q
            OR (CAST(:ph AS text) IS NOT NULL AND regexp_replace(coalesce(c.phone, ''), '\\D', '', 'g') LIKE :ph)
            OR EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id
                        AND (o.number ILIKE :q OR o.contact_name ILIKE :q)))""")
        params.update(q=f"%{q}%", ph=f"%{digits[-10:]}%" if len(digits) >= 4 else None)
    if show == "blocked":
        conds.append("c.is_blocked")
    elif show == "buyers":
        conds.append("EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)")
    elif show == "idle":
        conds.append("NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.id)")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = (await session.execute(text(f"SELECT count(*) FROM customers c {where}"), params)).scalar()
    page = max(page, 1)
    rows = await session.execute(text(f"""
        SELECT c.id, c.name, c.phone, c.email, c.created_at, c.last_login_at, c.is_blocked,
               c.personal_discount, c.email_verified_at IS NOT NULL AS email_ok,
               (SELECT count(*) FROM orders o WHERE o.customer_id = c.id) AS orders,
               (SELECT coalesce(sum(o.total), 0) FROM orders o WHERE o.customer_id = c.id
                   AND o.paid_at IS NOT NULL AND o.status <> 'cancelled') AS spent,
               (SELECT coalesce(sum(b.amount), 0) FROM bonus_ledger b WHERE b.customer_id = c.id) AS bonus,
               (SELECT string_agg(i.provider, ',') FROM customer_identities i WHERE i.customer_id = c.id) AS providers
          FROM customers c {where}
         ORDER BY {CUSTOMER_SORT.get(sort, CUSTOMER_SORT['new'])}, c.id DESC
         LIMIT :lim OFFSET :off"""), {**params, "lim": PAGE_SIZE, "off": (page - 1) * PAGE_SIZE})
    counts = (await session.execute(text("""
        SELECT count(*) AS all, count(*) FILTER (WHERE is_blocked) AS blocked,
               count(*) FILTER (WHERE created_at > now() - interval '30 days') AS month
          FROM customers"""))).first()
    return {"items": [dict(r._mapping) for r in rows], "total": total, "page": page,
            "pages": max(1, -(-total // PAGE_SIZE)), "counts": dict(counts._mapping)}


async def _customer(session: AsyncSession, customer_id: int):
    row = (await session.execute(text("SELECT * FROM customers WHERE id = :c"), {"c": customer_id})).first()
    if not row:
        raise HTTPException(404, "Покупатель не найден")
    return row


@router.get("/api/manage/customers/{customer_id}")
async def customer_card(customer_id: int, session: AsyncSession = Depends(get_session),
                        user=Depends(require_role("manager"))):
    c = await _customer(session, customer_id)
    orders = [{**dict(r._mapping), "label": ORDER_LABELS.get(r.status, r.status)}
              for r in await session.execute(text("""
        SELECT o.number, o.status::text AS status, o.total, o.created_at, o.paid_at,
               o.delivery_method, o.delivery_city,
               (SELECT count(*) FROM order_items oi WHERE oi.order_id = o.id) AS items
          FROM orders o WHERE o.customer_id = :c ORDER BY o.created_at DESC LIMIT 100"""), {"c": customer_id})]
    logins = [{"at": r.at, "device": useragent.device(r.user_agent), "method": METHODS.get(r.method, r.method)}
              for r in await session.execute(text("""
        SELECT at, user_agent, method FROM customer_logins WHERE customer_id = :c
         ORDER BY at DESC LIMIT 10"""), {"c": customer_id})]
    addresses = [dict(r._mapping) for r in await session.execute(text("""
        SELECT title, city, street, house, flat, is_default FROM customer_addresses
         WHERE customer_id = :c ORDER BY is_default DESC, created_at DESC"""), {"c": customer_id})]
    identities = [{"provider": METHODS.get(r.provider, r.provider), "display": r.display}
                  for r in await session.execute(text("""
        SELECT provider, display FROM customer_identities WHERE customer_id = :c"""), {"c": customer_id})]
    stats = (await session.execute(text("""
        SELECT count(*) AS orders,
               count(*) FILTER (WHERE status = 'completed') AS completed,
               count(*) FILTER (WHERE status = 'cancelled') AS cancelled,
               coalesce(sum(total) FILTER (WHERE paid_at IS NOT NULL AND status <> 'cancelled'), 0) AS spent,
               max(created_at) AS last_order
          FROM orders WHERE customer_id = :c"""), {"c": customer_id})).first()
    return {
        "customer": {
            "id": c.id, "name": c.name, "phone": c.phone, "email": c.email,
            "email_verified": c.email_verified_at is not None, "has_password": bool(c.password_hash),
            "created_at": c.created_at, "last_login_at": c.last_login_at,
            "is_blocked": c.is_blocked, "blocked_reason": c.blocked_reason, "staff_note": c.staff_note,
            "personal_discount": str(c.personal_discount), "notify_orders": c.notify_orders,
            "notify_promo": c.notify_promo,
        },
        "stats": dict(stats._mapping), "orders": orders, "logins": logins, "addresses": addresses,
        "identities": identities, "bonus": await loyalty.balance(session, customer_id),
        "audit": await audit_of(session, "customer", customer_id),
    }


class CustomerPatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)
    staff_note: str | None = Field(default=None, max_length=2000)


@router.patch("/api/manage/customers/{customer_id}")
async def customer_patch(customer_id: int, payload: CustomerPatch,
                         session: AsyncSession = Depends(get_session),
                         user=Depends(require_role("manager"))):
    """Контакты покупателя и заметка для своих. Телефон и email — вход
    в кабинет, поэтому оба уникальны: чужой номер не присвоить."""
    c = await _customer(session, customer_id)
    sent = payload.model_fields_set
    upd: dict = {}
    if "name" in sent:
        name = (payload.name or "").strip() or None
        require(people.check_name(name))
        upd["name"] = name
    if "phone" in sent:
        raw = (payload.phone or "").strip()
        phone = people.normalize_phone(raw) if raw else None
        if raw and not phone:
            raise HTTPException(422, "Телефон в формате +7 900 000-00-00")
        upd["phone"] = phone
    if "email" in sent:
        raw = (payload.email or "").strip()
        email = people.normalize_email(raw) if raw else None
        if raw and not email:
            raise HTTPException(422, "Email в формате name@example.ru")
        upd["email"] = email
    if "staff_note" in sent:
        upd["staff_note"] = (payload.staff_note or "").strip() or None
    if not upd.get("phone", c.phone) and not upd.get("email", c.email):
        raise HTTPException(422, "Нужен телефон или email — иначе покупателю не войти")
    for f, label in (("phone", "Этот телефон"), ("email", "Этот email")):
        if upd.get(f) and upd[f] != getattr(c, f):
            col = "phone = :v" if f == "phone" else "lower(email) = lower(:v)"
            if (await session.execute(text(f"SELECT 1 FROM customers WHERE {col} AND id <> :c"),
                                      {"v": upd[f], "c": customer_id})).first():
                raise HTTPException(409, f"{label} уже у другого покупателя")
    labels = {"name": "Имя", "phone": "Телефон", "email": "Email"}
    changes = [f"{labels[k]}: {getattr(c, k) or '—'} → {v or '—'}" for k, v in upd.items()
               if k != "staff_note" and (getattr(c, k) or None) != v]
    if "email" in upd and upd["email"] != c.email:
        # Новый адрес никто не подтверждал — письма о заказах ждут подтверждения
        upd["email_verified_at"] = None
    if upd:
        sets = ", ".join(f"{k} = :{k}" for k in upd)
        await session.execute(text(f"UPDATE customers SET {sets} WHERE id = :id"), {**upd, "id": customer_id})
    if changes:
        await audit(session, user, "customer", customer_id, "Контакты", "; ".join(changes))
    if "staff_note" in upd and upd["staff_note"] != c.staff_note:
        await audit(session, user, "customer", customer_id, "Заметка", upd["staff_note"] or "удалена")
    await session.commit()
    return {"ok": True}


class BlockIn(BaseModel):
    blocked: bool
    reason: str | None = Field(default=None, max_length=300)


@router.post("/api/manage/customers/{customer_id}/block")
async def customer_block(customer_id: int, payload: BlockIn, session: AsyncSession = Depends(get_session),
                         user=Depends(require_role("manager"))):
    """Блокировка закрывает вход и оформление и выкидывает из всех сеансов.
    Причина обязательна — её увидит следующий сотрудник."""
    await _customer(session, customer_id)
    reason = (payload.reason or "").strip()
    if payload.blocked and len(reason) < 3:
        raise HTTPException(422, "Напишите причину блокировки")
    await session.execute(text("""
        UPDATE customers SET is_blocked = :b, blocked_reason = :r,
               session_version = session_version + CASE WHEN :b THEN 1 ELSE 0 END
         WHERE id = :c"""), {"b": payload.blocked, "r": reason or None if payload.blocked else None,
                             "c": customer_id})
    await audit(session, user, "customer", customer_id,
                "Заблокирован" if payload.blocked else "Разблокирован", reason or None)
    await session.commit()
    return {"ok": True}


@router.post("/api/manage/customers/{customer_id}/logout")
async def customer_logout_all(customer_id: int, session: AsyncSession = Depends(get_session),
                              user=Depends(require_role("manager"))):
    """«Выйти на всех устройствах»: телефон потерян, вход кто-то подсмотрел."""
    await _customer(session, customer_id)
    await session.execute(text("UPDATE customers SET session_version = session_version + 1 WHERE id = :c"),
                          {"c": customer_id})
    await audit(session, user, "customer", customer_id, "Завершены все сеансы")
    await session.commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Сотрудники
# ------------------------------------------------------------------


@router.get("/api/manage/staff")
async def staff_list(session: AsyncSession = Depends(get_session), user=Depends(current_user)):
    """Админ видит всех; остальные — только себя (правят свои контакты
    и письма о заказах)."""
    rows = await session.execute(text("""
        SELECT u.id, u.login, u.full_name, u.role::text AS role, u.is_active, u.branch_id,
               u.email, u.phone, u.notify_orders, u.created_at, u.last_login_at,
               (SELECT b.city || ', ' || b.name FROM branches b WHERE b.id = u.branch_id) AS branch,
               (SELECT count(*) FROM orders o WHERE o.manager_id = u.id
                   AND o.status NOT IN ('completed', 'cancelled')) AS open_orders
          FROM users u
         WHERE CAST(:only AS int) IS NULL OR u.id = CAST(:only AS int)
         ORDER BY u.is_active DESC, u.role, coalesce(u.full_name, u.login)"""),
        {"only": None if user["role"] == "admin" else user["id"]})
    branches = [dict(r._mapping) for r in await session.execute(text("""
        SELECT id, city || ', ' || name AS title FROM branches WHERE is_active ORDER BY sort_order, city, name"""))]
    return {"items": [dict(r._mapping) for r in rows], "me": user["id"], "is_admin": user["role"] == "admin",
            "roles": {k: v["label"] for k, v in ROLE_INFO.items()}, "branches": branches}


class StaffIn(BaseModel):
    login: str | None = Field(default=None, max_length=32)
    full_name: str | None = Field(default=None, max_length=80)
    role: str | None = None
    branch_id: int | None = None
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    notify_orders: bool | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, max_length=200)


def check_password(pw: str) -> None:
    if len(pw) < 8:
        raise HTTPException(422, "Пароль сотрудника — не короче 8 символов")
    require(people.check_new_password(pw))


async def _staff_fields(session: AsyncSession, p: StaffIn, sent: set) -> dict:
    """Проверка полей сотрудника — общая для создания и правки."""
    upd: dict = {}
    if "full_name" in sent:
        name = (p.full_name or "").strip() or None
        require(people.check_name(name))
        upd["full_name"] = name
    if "role" in sent:
        if p.role not in ROLE_INFO:
            raise HTTPException(422, "Роль: администратор, менеджер или разборщик")
        upd["role"] = p.role
    if "branch_id" in sent:
        if p.branch_id is not None and not (await session.execute(
                text("SELECT 1 FROM branches WHERE id = :b"), {"b": p.branch_id})).first():
            raise HTTPException(422, "Нет такого филиала")
        upd["branch_id"] = p.branch_id
    if "email" in sent:
        raw = (p.email or "").strip()
        email = people.normalize_email(raw) if raw else None
        if raw and not email:
            raise HTTPException(422, "Email в формате name@example.ru")
        upd["email"] = email
    if "phone" in sent:
        raw = (p.phone or "").strip()
        phone = people.normalize_phone(raw) if raw else None
        if raw and not phone:
            raise HTTPException(422, "Телефон в формате +7 900 000-00-00")
        upd["phone"] = phone
    if "notify_orders" in sent and p.notify_orders is not None:
        upd["notify_orders"] = p.notify_orders
    return upd


@router.post("/api/manage/staff", status_code=201)
async def staff_create(payload: StaffIn, session: AsyncSession = Depends(get_session),
                       user=Depends(require_role("admin"))):
    login = (payload.login or "").strip().lower()
    if not LOGIN_RE.match(login):
        raise HTTPException(422, "Логин: латиница, цифры, точка, дефис — от 3 до 32 знаков")
    if (await session.execute(text("SELECT 1 FROM users WHERE login = :l"), {"l": login})).first():
        raise HTTPException(409, "Такой логин уже есть")
    check_password(payload.password or "")
    sent = payload.model_fields_set | {"role"}
    if not payload.role:
        payload.role = "manager"
    upd = await _staff_fields(session, payload, sent)
    if upd.get("notify_orders") and not upd.get("email"):
        raise HTTPException(422, "Для писем о заказах укажите email")
    cols = {"login": login, "password_hash": hash_password(payload.password), **upd}
    uid = (await session.execute(text(f"""
        INSERT INTO users ({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)})
        RETURNING id"""), cols)).scalar_one()
    await audit(session, user, "staff", uid, "Заведён", f"{login}, {ROLE_INFO[cols['role']]['label']}")
    await session.commit()
    return {"id": uid}


@router.patch("/api/manage/staff/{staff_id}")
async def staff_patch(staff_id: int, payload: StaffIn, session: AsyncSession = Depends(get_session),
                      user=Depends(current_user)):
    """Админ правит всех; остальные — только себя и только контакты,
    имя и письма о заказах: роль, филиал и доступ себе не выдать."""
    sent = payload.model_fields_set - {"login"}
    is_admin = user["role"] == "admin"
    if not is_admin and (staff_id != user["id"] or sent - {"full_name", "email", "phone", "notify_orders"}):
        raise HTTPException(403, "Недостаточно прав")
    row = (await session.execute(text("SELECT * FROM users WHERE id = :u"), {"u": staff_id})).first()
    if not row:
        raise HTTPException(404, "Сотрудник не найден")
    upd = await _staff_fields(session, payload, sent)
    if "is_active" in sent and payload.is_active is not None:
        upd["is_active"] = payload.is_active
    # Себя не отключить и не понизить: можно запереть бэкенд изнутри
    if staff_id == user["id"] and (upd.get("is_active") is False or upd.get("role", row.role) != row.role):
        raise HTTPException(409, "Свою роль и доступ меняет другой администратор")
    losing_admin = row.role == "admin" and row.is_active and (
        upd.get("role", "admin") != "admin" or upd.get("is_active") is False)
    if losing_admin and not (await session.execute(text("""
            SELECT 1 FROM users WHERE role = 'admin' AND is_active AND id <> :u"""), {"u": staff_id})).first():
        raise HTTPException(409, "Это последний администратор — сначала назначьте другого")
    if upd.get("notify_orders", row.notify_orders) and not upd.get("email", row.email):
        raise HTTPException(422, "Для писем о заказах укажите email")
    if "password" in payload.model_fields_set and payload.password:
        check_password(payload.password)
        upd["password_hash"] = hash_password(payload.password)
    changes = []
    labels = {"full_name": "Имя", "role": "Роль", "branch_id": "Филиал", "email": "Email",
              "phone": "Телефон", "notify_orders": "Письма о заказах", "is_active": "Доступ"}
    for k, v in upd.items():
        if k == "password_hash" or getattr(row, k) == v:
            continue
        old, new = getattr(row, k), v
        if k == "role":
            old, new = ROLE_INFO[old]["label"], ROLE_INFO[new]["label"]
        elif k in ("notify_orders", "is_active"):
            old, new = ("да" if old else "нет"), ("да" if new else "нет")
        elif k == "branch_id":
            names = {r.id: r.t for r in await session.execute(text(
                "SELECT id, city || ', ' || name AS t FROM branches"))}
            old, new = names.get(old, "все"), names.get(new, "все")
        changes.append(f"{labels[k]}: {old or '—'} → {new or '—'}")
    # Пароль сменили или доступ закрыли — прежние входы недействительны.
    # Роль перечитывается при каждом запросе — ради неё выкидывать незачем
    if "password_hash" in upd or upd.get("is_active") is False:
        upd["session_version"] = row.session_version + 1
    if upd:
        sets = ", ".join(f"{k} = :{k}" for k in upd)
        await session.execute(text(f"UPDATE users SET {sets} WHERE id = :id"), {**upd, "id": staff_id})
    if changes:
        await audit(session, user, "staff", staff_id, "Изменён", "; ".join(changes))
    if "password_hash" in upd:
        await audit(session, user, "staff", staff_id, "Новый пароль",
                    "входы на всех устройствах завершены")
    await session.commit()
    return {"ok": True}


@router.post("/api/manage/staff/{staff_id}/logout")
async def staff_logout_all(staff_id: int, session: AsyncSession = Depends(get_session),
                           user=Depends(require_role("admin"))):
    n = (await session.execute(text("""
        UPDATE users SET session_version = session_version + 1 WHERE id = :u"""), {"u": staff_id})).rowcount
    if not n:
        raise HTTPException(404, "Сотрудник не найден")
    await audit(session, user, "staff", staff_id, "Завершены все сеансы")
    await session.commit()
    return {"ok": True}


@router.get("/api/manage/staff/{staff_id}/audit")
async def staff_audit(staff_id: int, session: AsyncSession = Depends(get_session),
                      user=Depends(require_role("admin"))):
    return await audit_of(session, "staff", staff_id)
