"""Точка входа.

Запуск:
    uvicorn app.main:app --host 127.0.0.1 --port 8100

Наружу смотрит Caddy, он же выдаёт HTTPS и отдаёт /media и /static
напрямую с диска — Python на статике только тратит воркеры.
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import authenticate, drop_session, issue_session
from .config import settings
from .database import check_connection, dispose, get_session
from .routers import admin, catalog, dismantle, intake, manage, reference, stock
from .templating import templates

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("razbor")
# Pillow при первом импорте перечисляет все свои плагины — в лог это не нужно
logging.getLogger("PIL").setLevel(logging.INFO)


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

# Защита от перебора пароля. Считаем неудачи по паре «IP + логин»:
# по одному IP работает целая смена через общий роутер, а по одному
# логину перебирают с разных адресов — блокировать надо пересечение.
#
# Счётчик в памяти процесса: сотрудников единицы, ради этого поднимать
# Redis незачем. Цена — при нескольких воркерах лимит умножается на их
# число, и перезапуск обнуляет счёт. От автоматического перебора
# защищает всё равно, от точечного подбора живым человеком — нет.
LOGIN_MAX_FAILS = 7
LOGIN_BLOCK_SECONDS = 300
_login_fails: dict[tuple[str, str], list[float]] = {}


def _login_key(request: Request, login: str) -> tuple[str, str]:
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not ip:
        ip = request.client.host if request.client else "?"
    return ip, login.strip().lower()


def login_blocked(key: tuple[str, str]) -> bool:
    now = time.monotonic()
    fails = [t for t in _login_fails.get(key, []) if now - t < LOGIN_BLOCK_SECONDS]
    # Заодно чистим просроченные: без этого словарь растёт на каждой опечатке
    if fails:
        _login_fails[key] = fails
    else:
        _login_fails.pop(key, None)
    return len(fails) >= LOGIN_MAX_FAILS


def login_failed(key: tuple[str, str]) -> None:
    _login_fails.setdefault(key, []).append(time.monotonic())


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
    key = _login_key(request, login)
    if login_blocked(key):
        log.warning("Перебор пароля: %s / %s", key[0], key[1])
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "next": next,
             "error": "Слишком много попыток. Подождите 5 минут."},
            status_code=429,
        )

    user = await authenticate(session, login, password)
    if not user:
        login_failed(key)
        # Не уточняем, что именно неверно — логин или пароль
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "next": next, "error": "Неверный логин или пароль"},
            status_code=401,
        )

    _login_fails.pop(key, None)  # вошёл — счёт обнуляем

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
