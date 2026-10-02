"""Полки склада: QR-этикетки и что на полке лежит.

Место хранения у детали — просто текст («Б-3»). Набирать его руками —
значит опечатываться: «Б-3», «б3», «Б 3» — для поиска это три полки.
QR на полке решает это: приложение сотрудника сканирует деталь, потом
полку, и место записывается ровно таким, как напечатано.

В QR — ссылка /shelf/<код>: обычная камера телефона откроет страницу со
списком деталей на полке, приложение узнает в ней полку.
"""

import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user
from ..config import settings
from ..database import get_session
from ..templating import templates
from .dismantle import qr_svg

router = APIRouter(tags=["shelves"])

MAX_LABELS = 200
CODE_MAX = 40
# «А-1..А-20» и «А-1..20» — диапазон с общим началом
RANGE_RE = re.compile(r"^(.*?)(\d+)\s*\.\.\s*(?:\1)?(\d+)$")


def normalize(code: str) -> str:
    """Код полки так, как его хранят: без лишних пробелов, буквы заглавные."""
    return re.sub(r"\s+", " ", code).strip().upper()[:CODE_MAX]


def parse_codes(raw: str) -> list[str]:
    """«А-1..А-5, Б-1; В-2» → список кодов. Диапазон раскрывается по числу
    в конце: А-1..А-5 → А-1 … А-5. Больше MAX_LABELS — ошибка: такой
    лист проще разбить, чем печатать по ошибке тысячу этикеток."""
    out: list[str] = []
    for part in re.split(r"[,;\n]+", raw or ""):
        part = part.strip()
        if not part:
            continue
        m = RANGE_RE.match(part)
        if m:
            prefix, a, b = m.group(1), int(m.group(2)), int(m.group(3))
            if a > b:
                a, b = b, a
            if b - a + 1 > MAX_LABELS:
                raise ValueError(f"В диапазоне {part} больше {MAX_LABELS} полок")
            out.extend(normalize(f"{prefix}{n}") for n in range(a, b + 1))
        else:
            out.append(normalize(part))
    # Повторы печатать незачем, порядок — как ввели
    seen: set[str] = set()
    codes = [c for c in out if c and not (c in seen or seen.add(c))]
    if len(codes) > MAX_LABELS:
        raise ValueError(f"За раз — не больше {MAX_LABELS} этикеток")
    return codes


def shelf_url(code: str) -> str:
    return f"{settings.base_url}/shelf/{quote(code, safe='')}"


@router.get("/shelves/labels", response_class=HTMLResponse)
async def shelf_labels(request: Request, codes: str = "", user=Depends(current_user)):
    """Этикетки полок: вводят список, печатают лист, клеят на стеллаж."""
    error = None
    labels = []
    try:
        labels = [{"code": c, "qr": qr_svg(shelf_url(c), size=4)} for c in parse_codes(codes)]
    except ValueError as e:
        error = str(e)
    return templates.TemplateResponse(
        "admin/shelf_labels.html",
        {"request": request, "user": user, "codes": codes, "labels": labels, "error": error},
    )


async def parts_on_shelf(session: AsyncSession, code: str) -> list[dict]:
    """Что числится на полке — без проданных и списанных: их на полке нет."""
    rows = await session.execute(
        text("""
        SELECT p.id, p.sku, p.name, p.status::text AS status, p.quantity,
               p.price, p.location,
               (SELECT br.city || ', ' || br.name FROM branches br
                 WHERE br.id = p.branch_id) AS branch
          FROM parts p
         WHERE upper(btrim(p.location)) = :code
           AND p.status IN ('draft', 'in_stock', 'reserved')
         ORDER BY p.sku
    """),
        {"code": normalize(code)},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/shelf/{code}", response_class=HTMLResponse)
async def shelf_page(
    request: Request,
    code: str,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    """Куда ведёт QR полки, если навести обычную камеру: список на полке."""
    code = normalize(code)
    if not code:
        raise HTTPException(404, "Полка не указана")
    return templates.TemplateResponse(
        "admin/shelf.html",
        {"request": request, "user": user, "code": code,
         "parts": await parts_on_shelf(session, code)},
    )


@router.get("/api/manage/shelves/{code}/parts")
async def shelf_parts_api(
    code: str,
    session: AsyncSession = Depends(get_session),
    user=Depends(current_user),
):
    """То же для приложения: инвентаризация сверяет полку с этим списком."""
    return await parts_on_shelf(session, code)
