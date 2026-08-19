"""Точка входа.

Запуск:
    uvicorn app.main:app --host 127.0.0.1 --port 8100

Наружу смотрит Caddy, он же выдаёт HTTPS и отдаёт /media и /static
напрямую с диска — Python на статике только тратит воркеры.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import authenticate, drop_session, issue_session
from .config import settings
from .database import check_connection, dispose, get_session
from .routers import admin, catalog, dismantle, intake, manage, reference, stock

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("razbor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.media_root.mkdir(parents=True, exist_ok=True)
    (settings.media_root / "donors").mkdir(exist_ok=True)
    (settings.media_root / "parts").mkdir(exist_ok=True)

    if not await check_connection():
        log.error("Нет связи с базой — проверьте database_url в .env")
    else:
        log.info("База отвечает, приложение поднято")

    yield
    await dispose()


app = FastAPI(
    title=settings.app_name,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.debug else None,
    lifespan=lifespan,
)

templates = Jinja2Templates(directory="templates")
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["base_url"] = settings.base_url

# Дублируют Caddy: нужны при локальной разработке без прокси
app.mount("/static", StaticFiles(directory=settings.static_root), name="static")
app.mount("/media", StaticFiles(directory=settings.media_root), name="media")

app.include_router(catalog.router)
app.include_router(intake.router)
app.include_router(dismantle.router)
app.include_router(reference.router)
app.include_router(admin.router)
app.include_router(manage.router)
app.include_router(stock.router)


# ------------------------------------------------------------------
# Вход
# ------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/admin"):
    return templates.TemplateResponse(
        "admin/login.html", {"request": request, "next": next, "error": None}
    )


@app.post("/login")
async def login_submit(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    next: str = Form("/admin"),
    session: AsyncSession = Depends(get_session),
):
    user = await authenticate(session, login, password)
    if not user:
        # Не уточняем, что именно неверно — логин или пароль
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "next": next, "error": "Неверный логин или пароль"},
            status_code=401,
        )

    # Открытый редирект: пускаем только на внутренние пути
    target = next if next.startswith("/") and not next.startswith("//") else "/admin"
    response = RedirectResponse(target, status_code=303)
    issue_session(response, user["id"], user["role"])
    log.info("Вход: %s (%s)", user["login"], user["role"])
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    drop_session(response)
    return response


# ------------------------------------------------------------------
# Служебное
# ------------------------------------------------------------------


@app.get("/healthz")
async def healthz():
    ok = await check_connection()
    return JSONResponse({"db": ok}, status_code=200 if ok else 503)


@app.get("/robots.txt", response_class=HTMLResponse)
async def robots():
    return Response(
        "User-agent: *\n"
        "Disallow: /admin\n"
        "Disallow: /intake\n"
        "Disallow: /donors\n"
        "Disallow: /login\n"
        "Disallow: /api/\n"
        f"Sitemap: {settings.base_url}/sitemap.xml\n",
        media_type="text/plain",
    )


# ------------------------------------------------------------------
# Ошибки
# ------------------------------------------------------------------


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    # API отвечает JSON, страницы — человеческой страницей
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    if exc.status_code == 401:
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "code": exc.status_code,
            "detail": exc.detail,
            "user": None,
        },
        status_code=exc.status_code,
    )


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError):
    """Нарушение целостности — почти всегда ссылка на несуществующую
    запись или дубль. Пользователю нужен смысл, а не трассировка."""
    detail = "Не удалось сохранить: данные не сходятся"
    orig = str(getattr(exc, "orig", "")).lower()
    if "foreign key" in orig or "fkey" in orig:
        detail = "Выбранная запись справочника не найдена — обновите страницу"
    elif "unique" in orig or "duplicate" in orig:
        detail = "Такая запись уже существует"

    log.warning("Целостность на %s: %s", request.url.path, exc.orig)

    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": detail}, status_code=409)
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "code": 409, "detail": detail, "user": None},
        status_code=409,
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Необработанная ошибка на %s", request.url.path)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Внутренняя ошибка сервера"}, status_code=500)
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "code": 500,
            "detail": "Что-то сломалось на нашей стороне. Мы уже знаем.",
            "user": None,
        },
        status_code=500,
    )
