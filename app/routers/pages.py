"""Страницы витрины, которые обещаны ссылками, но их не было.

Меню вело на /delivery и /contacts, кнопка «Задать вопрос» на карточке
детали — туда же, robots.txt объявлял /sitemap.xml. Все четыре адреса
отдавали 404: покупатель упирался в ошибку ровно там, где собирался
написать или позвонить.

Здесь же приём заявок. Таблица leads в схеме была с самого начала,
но писать в неё было некому — форма заявки не существовала.
"""

import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import optional_user
from ..config import settings
from ..database import get_session
from ..templating import templates

router = APIRouter(tags=["pages"])


async def branches(session: AsyncSession) -> list[tuple[str, list[dict]]]:
    """Филиалы по городам: адрес покупателю нужен и в контактах,
    и в доставке — это один и тот же список.

    Группируем здесь, а не фильтром groupby в шаблоне: тот сортирует
    города по алфавиту и Москва встаёт впереди Перми, хотя порядок
    филиалов задан вручную полем sort_order.
    """
    rows = await session.execute(
        text("""
        SELECT city, name, address, phone
          FROM branches
         WHERE is_active
         ORDER BY sort_order, city
    """)
    )

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r.city, []).append(dict(r._mapping))
    return list(grouped.items())


@router.get("/contacts", response_class=HTMLResponse)
async def contacts(
    request: Request,
    about: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """about=<артикул> приходит с карточки детали: человек нажал
    «Задать вопрос», и спрашивать он будет про неё."""
    part = None
    if about:
        row = (
            await session.execute(
                text("SELECT sku, name FROM parts WHERE sku = :s"), {"s": about}
            )
        ).first()
        part = dict(row._mapping) if row else None

    return templates.TemplateResponse(
        "contacts.html",
        {
            "request": request,
            "user": await optional_user(request, session),
            "branches": await branches(session),
            "part": part,
        },
    )


@router.get("/delivery", response_class=HTMLResponse)
async def delivery(request: Request, session: AsyncSession = Depends(get_session)):
    return templates.TemplateResponse(
        "delivery.html",
        {
            "request": request,
            "user": await optional_user(request, session),
            "branches": await branches(session),
            "reserve_hours": settings.reserve_hours,
        },
    )


# ------------------------------------------------------------------
# Заявка
# ------------------------------------------------------------------

# Публичная ручка без входа, значит её будут долбить. Счёт по IP
# в памяти процесса: от робота, который шлёт заявку в цикле, спасает,
# от распределённого спама — нет, для этого нужен общий счётчик
LEAD_MAX = 5
LEAD_WINDOW = 600
_lead_hits: dict[str, list[float]] = {}


def _too_many(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _lead_hits.get(ip, []) if now - t < LEAD_WINDOW]
    if hits:
        _lead_hits[ip] = hits
    else:
        _lead_hits.pop(ip, None)
    return len(hits) >= LEAD_MAX


class Lead(BaseModel):
    phone: str = Field(max_length=40)
    name: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=2000)
    sku: str | None = Field(default=None, max_length=32)
    # Вопрос со страницы машины: «что можно снять под заказ»
    donor: str | None = Field(default=None, max_length=16)


@router.post("/api/leads", status_code=201)
async def create_lead(
    payload: Lead, request: Request, session: AsyncSession = Depends(get_session)
):
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "?"))
    if _too_many(ip):
        raise HTTPException(429, "Слишком много заявок подряд. Позвоните нам.")

    # Телефон в любом виде, лишь бы можно было перезвонить: человек
    # пишет и +7 (912) 345-67-89, и 89123456789
    digits = re.sub(r"\D", "", payload.phone)
    if len(digits) < 10:
        raise HTTPException(422, "Проверьте номер телефона")

    part_id = None
    if payload.sku:
        row = (
            await session.execute(
                text("SELECT id FROM parts WHERE sku = :s"), {"s": payload.sku}
            )
        ).first()
        # Артикул мог устареть — заявку всё равно принимаем, иначе
        # потеряем покупателя из-за ссылки на проданную деталь
        part_id = row.id if row else None

    donor_id = None
    if payload.donor:
        # Так же, как с артикулом: машину могли снять с витрины, пока
        # человек писал, — вопрос всё равно принимаем
        donor_id = (
            await session.execute(
                text("SELECT id FROM donors WHERE code = :c"),
                {"c": payload.donor.strip().upper()},
            )
        ).scalar()

    await session.execute(
        text("""
        INSERT INTO leads (part_id, donor_id, phone, name, message)
        VALUES (:p, :d, :phone, :name, :msg)
    """),
        {
            "p": part_id,
            "d": donor_id,
            "phone": payload.phone.strip(),
            "name": (payload.name or "").strip() or None,
            "msg": (payload.message or "").strip() or None,
        },
    )
    await session.commit()

    _lead_hits.setdefault(ip, []).append(time.monotonic())
    return {"ok": True}


# ------------------------------------------------------------------
# Карта сайта
# ------------------------------------------------------------------


@router.get("/sitemap.xml")
async def sitemap(session: AsyncSession = Depends(get_session)):
    """robots.txt ссылается сюда с самого начала. Отдаём страницы
    и карточки того, что реально лежит на складе: продано — убрали,
    иначе поисковик приведёт человека на 404."""
    rows = await session.execute(
        text("""
        SELECT sku, updated_at
          FROM parts
         WHERE published AND status = 'in_stock'
         ORDER BY updated_at DESC NULLS LAST
         LIMIT 50000
    """)
    )

    urls = [f"<url><loc>{settings.base_url}{p}</loc></url>"
            for p in ("/", "/catalog", "/cars", "/delivery", "/contacts")]
    for r in await session.execute(text(
            "SELECT code FROM donors WHERE status IN ('accepted', 'dismantling', 'dismantled')")):
        urls.append(f"<url><loc>{settings.base_url}/cars/{r.code}</loc></url>")
    for r in rows:
        stamp = f"<lastmod>{r.updated_at.date()}</lastmod>" if r.updated_at else ""
        urls.append(f"<url><loc>{settings.base_url}/p/{r.sku}</loc>{stamp}</url>")

    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls)
            + "\n</urlset>\n")
    return Response(body, media_type="application/xml")
