"""Быстрый вход покупателя: Google, VK ID, Telegram, MAX.

Каждый способ включается своими ключами в .env: нет ключей — нет
кнопки, и ручки отвечают 404. Итог у всех один — identity_login()
находит или заводит покупателя, дальше та же кука, что и при входе
по паролю, и та же передача корзины.

Google и VK — обычный OAuth с переходом на сайт провайдера и обратно.
state и PKCE-ключ живут в подписанной куке на десять минут: вернулся
не тот браузер или прошло много времени — вход не засчитывается.

Telegram — Login Widget: Telegram сам спрашивает подтверждение и
возвращает данные с подписью, которую проверяем токеном бота.

MAX — OAuth у мессенджера нет, вход идёт через бота. Сайт выдаёт
одноразовый код, человек открывает бота по ссылке с ним, бот
присылает кнопку «Войти на сайт», и только нажатие подтверждает вход.
Без кнопки злоумышленник мог бы прислать жертве ссылку со своим кодом:
жертва открыла бота — и вошёл бы злоумышленник, в кабинет жертвы.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import customer_auth as ca
from ..config import settings
from ..database import get_session
from ..templating import templates
from .dismantle import qr_svg
from .shop import adopt_cart, cart_token

log = logging.getLogger("razbor.oauth")
router = APIRouter(tags=["oauth"])

STATE_COOKIE = "razbor_oauth"
STATE_TTL = 600
state_signer = URLSafeTimedSerializer(settings.secret_key, salt="razbor-oauth")

MAX_API = "https://platform-api2.max.ru"
MAX_TTL_MIN = 5

PROVIDERS = {
    "google": "Google",
    "vk": "VK ID",
    "telegram": "Telegram",
    "max": "MAX",
}


def enabled() -> list[dict]:
    """Какие кнопки показать на странице входа."""
    on = {
        "google": bool(settings.google_client_id and settings.google_client_secret),
        "vk": bool(settings.vk_client_id),
        "telegram": bool(settings.telegram_bot_name and settings.telegram_bot_token),
        "max": bool(settings.max_bot_name and settings.max_bot_token),
    }
    return [{"code": k, "label": v} for k, v in PROVIDERS.items() if on[k]]


def is_enabled(provider: str) -> bool:
    return any(p["code"] == provider for p in enabled())


def safe_next(raw: str | None) -> str:
    """Возврат только на свой сайт: //evil.ru и https://… — открытый
    редирект, которым ссылку входа превратили бы в фишинговую."""
    if raw and raw.startswith("/") and not raw.startswith("//") and "\\" not in raw:
        return raw
    return "/account"


def callback_url(provider: str) -> str:
    return f"{settings.base_url}/auth/{provider}/callback"


def fail(message: str) -> RedirectResponse:
    return RedirectResponse("/account/login?" + urlencode({"error": message}), status_code=303)


async def finish(request: Request, session: AsyncSession, customer_id: int,
                 next_url: str) -> RedirectResponse:
    """Общий конец любого входа: корзина — покупателю, кука — браузеру."""
    await adopt_cart(session, cart_token(request), customer_id)
    response = RedirectResponse(safe_next(next_url), status_code=303)
    ca.issue(response, customer_id)
    response.delete_cookie(STATE_COOKIE, path="/auth")
    return response


def read_state(request: Request, provider: str, state: str | None) -> dict | None:
    token = request.cookies.get(STATE_COOKIE)
    if not token or not state:
        return None
    try:
        data = state_signer.loads(token, max_age=STATE_TTL)
    except (BadSignature, SignatureExpired):
        return None
    if data.get("p") != provider or not hmac.compare_digest(data.get("s", ""), state):
        return None
    return data


# ------------------------------------------------------------------
# Google и VK ID — OAuth с переходом к провайдеру
# ------------------------------------------------------------------


@router.get("/auth/google/start")
async def google_start(next: str = "/account"):
    return oauth_start("google", next)


@router.get("/auth/vk/start")
async def vk_start(next: str = "/account"):
    return oauth_start("vk", next)


def oauth_start(provider: str, next: str) -> RedirectResponse:
    if not is_enabled(provider):
        raise HTTPException(404, "Этот способ входа не подключён")

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    if provider == "google":
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
            "client_id": settings.google_client_id,
            "redirect_uri": callback_url("google"),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        })
    else:
        url = "https://id.vk.com/authorize?" + urlencode({
            "response_type": "code",
            "client_id": settings.vk_client_id,
            "redirect_uri": callback_url("vk"),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "email phone",
        })

    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        STATE_COOKIE,
        state_signer.dumps({"p": provider, "s": state, "v": verifier, "n": safe_next(next)}),
        max_age=STATE_TTL, httponly=True, secure=not settings.debug,
        # lax: кука должна вернуться с провайдера обычным переходом по ссылке
        samesite="lax", path="/auth",
    )
    return response


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    if not is_enabled("google"):
        raise HTTPException(404)
    if error:
        return fail("Вход через Google отменён")
    st = read_state(request, "google", state)
    if not st or not code:
        return fail("Вход через Google не удался — попробуйте ещё раз")

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            tok = await http.post("https://oauth2.googleapis.com/token", data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": callback_url("google"),
                "grant_type": "authorization_code",
                "code_verifier": st["v"],
            })
            tok.raise_for_status()
            # Профиль спрашиваем у Google напрямую по токену, а не читаем
            # из id_token: так не нужна проверка подписи JWT
            me = await http.get("https://openidconnect.googleapis.com/v1/userinfo",
                                headers={"Authorization": f"Bearer {tok.json()['access_token']}"})
            me.raise_for_status()
            u = me.json()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("Google: %s", e)
        return fail("Google не ответил — попробуйте ещё раз")

    cid = await ca.identity_login(
        session, "google", str(u["sub"]), name=u.get("name"),
        email=u.get("email"), email_verified=bool(u.get("email_verified")),
        display=u.get("email") or u.get("name"))
    return await finish(request, session, cid, st["n"])


@router.get("/auth/vk/callback")
async def vk_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    device_id: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    if not is_enabled("vk"):
        raise HTTPException(404)
    if error:
        return fail("Вход через VK отменён")
    st = read_state(request, "vk", state)
    if not st or not code or not device_id:
        return fail("Вход через VK не удался — попробуйте ещё раз")

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            tok = await http.post("https://id.vk.com/oauth2/auth", data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": st["v"],
                "client_id": settings.vk_client_id,
                "device_id": device_id,
                "redirect_uri": callback_url("vk"),
                "state": state,
            })
            tok.raise_for_status()
            t = tok.json()
            if "access_token" not in t:
                raise ValueError(t.get("error_description") or t.get("error") or "нет токена")
            me = await http.post("https://id.vk.com/oauth2/user_info", data={
                "client_id": settings.vk_client_id,
                "access_token": t["access_token"],
            })
            me.raise_for_status()
            u = me.json()["user"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("VK ID: %s", e)
        return fail("VK не ответил — попробуйте ещё раз")

    name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x)
    # Почту VK ID отдаёт только подтверждённую — иначе её нет в ответе
    cid = await ca.identity_login(
        session, "vk", str(u["user_id"]), name=name, email=u.get("email"),
        email_verified=bool(u.get("email")), phone=u.get("phone"), display=name)
    return await finish(request, session, cid, st["n"])


# ------------------------------------------------------------------
# Telegram Login Widget
# ------------------------------------------------------------------


def telegram_check(data: dict[str, str]) -> bool:
    """Подпись виджета: HMAC-SHA256 от строк «ключ=значение» по алфавиту,
    ключ — SHA256 от токена бота. Плюс свежесть: старую ссылку входа
    нельзя переиграть через сутки."""
    got = data.get("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()) if k != "hash")
    key = hashlib.sha256(settings.telegram_bot_token.encode()).digest()
    want = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    try:
        fresh = time.time() - int(data.get("auth_date", "0")) < 86400
    except ValueError:
        return False
    return fresh and hmac.compare_digest(want, got)


@router.get("/auth/telegram/callback")
async def telegram_callback(
    request: Request,
    next: str = "/account",
    session: AsyncSession = Depends(get_session),
):
    if not is_enabled("telegram"):
        raise HTTPException(404)
    fields = ("id", "first_name", "last_name", "username", "photo_url", "auth_date", "hash")
    data = {k: request.query_params[k] for k in fields if k in request.query_params}
    if "id" not in data or not telegram_check(data):
        return fail("Telegram не подтвердил вход — попробуйте ещё раз")

    name = " ".join(x for x in (data.get("first_name"), data.get("last_name")) if x)
    cid = await ca.identity_login(
        session, "telegram", data["id"], name=name,
        display="@" + data["username"] if data.get("username") else name)
    return await finish(request, session, cid, next)


# ------------------------------------------------------------------
# MAX — через бота
# ------------------------------------------------------------------


@router.get("/auth/max/start", response_class=HTMLResponse)
async def max_start(
    request: Request,
    next: str = "/account",
    session: AsyncSession = Depends(get_session),
):
    if not is_enabled("max"):
        raise HTTPException(404, "Вход через MAX не подключён")

    token = secrets.token_urlsafe(18)
    await session.execute(text("DELETE FROM max_logins WHERE created_at < now() - interval '1 day'"))
    await session.execute(text("INSERT INTO max_logins (token) VALUES (:t)"), {"t": token})
    await session.commit()

    link = f"https://max.ru/{settings.max_bot_name}?start={token}"
    response = templates.TemplateResponse("shop/login_max.html", {
        "request": request, "user": None, "customer": None,
        "link": link, "token": token, "qr": qr_svg(link), "minutes": MAX_TTL_MIN,
        "next": safe_next(next),
    })
    # Код привязан к браузеру, который его запросил: забрать вход
    # может только он, даже если ссылку кто-то подсмотрел
    response.set_cookie(STATE_COOKIE, state_signer.dumps({"p": "max", "s": token}),
                        max_age=MAX_TTL_MIN * 60, httponly=True,
                        secure=not settings.debug, samesite="lax", path="/auth")
    return response


@router.get("/auth/max/status")
async def max_status(
    request: Request,
    token: str,
    session: AsyncSession = Depends(get_session),
):
    """Страница ожидания спрашивает раз в пару секунд. Подтверждено —
    выдаём куку покупателя прямо в этом ответе."""
    if not is_enabled("max") or not read_state(request, "max", token):
        return JSONResponse({"state": "expired"})

    row = (await session.execute(text(f"""
        SELECT max_user_id, max_name, confirmed_at, used_at,
               created_at < now() - interval '{MAX_TTL_MIN} minutes' AS old
          FROM max_logins WHERE token = :t"""), {"t": token})).first()
    if not row or row.used_at or (row.old and not row.confirmed_at):
        return JSONResponse({"state": "expired"})
    if not row.confirmed_at:
        return JSONResponse({"state": "waiting"})

    # Забираем вход один раз: второй опрос того же кода ничего не даст
    took = (await session.execute(text("""
        UPDATE max_logins SET used_at = now() WHERE token = :t AND used_at IS NULL"""),
        {"t": token})).rowcount
    await session.commit()
    if not took:
        return JSONResponse({"state": "expired"})

    cid = await ca.identity_login(session, "max", str(row.max_user_id),
                                  name=row.max_name, display=row.max_name)
    await adopt_cart(session, cart_token(request), cid)
    response = JSONResponse({"state": "ok"})
    ca.issue(response, cid)
    response.delete_cookie(STATE_COOKIE, path="/auth")
    return response


async def max_call(method: str, path: str, params: dict | None = None,
                   json: dict | None = None) -> None:
    """Ответ бота человеку — дело вежливости: не дошёл, вход не ломается."""
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            r = await http.request(method, MAX_API + path, params=params, json=json,
                                   headers={"Authorization": settings.max_bot_token})
            if r.status_code >= 400:
                log.warning("MAX %s %s: %s %s", method, path, r.status_code, r.text[:200])
    except httpx.HTTPError as e:
        log.warning("MAX %s %s: %s", method, path, e)


@router.post("/auth/max/webhook")
async def max_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    """События бота. Секрет из заголовка сверяем с тем, что передали при
    подписке: без этого кто угодно подтвердил бы любой код."""
    if not is_enabled("max"):
        raise HTTPException(404)
    secret = request.headers.get("x-max-bot-api-secret", "")
    if not settings.max_webhook_secret or not hmac.compare_digest(
            secret, settings.max_webhook_secret):
        raise HTTPException(403)

    upd = await request.json()
    kind = upd.get("update_type")

    if kind == "bot_started" and upd.get("payload"):
        token, user = upd["payload"], upd.get("user") or {}
        alive = (await session.execute(text(f"""
            SELECT 1 FROM max_logins WHERE token = :t AND used_at IS NULL
               AND created_at > now() - interval '{MAX_TTL_MIN} minutes'"""),
            {"t": token})).first()
        if alive and user.get("user_id"):
            host = settings.base_url.split("://")[-1]
            await max_call("POST", "/messages", params={"user_id": user["user_id"]}, json={
                "text": f"Вход на сайт {host}. Нажмите кнопку, чтобы войти. "
                        "Если вход запрашивали не вы — ничего не нажимайте.",
                "attachments": [{"type": "inline_keyboard", "payload": {"buttons": [[
                    {"type": "callback", "text": "Войти на сайт", "payload": "login:" + token},
                ]]}}],
            })

    elif kind == "message_callback":
        cb = upd.get("callback") or {}
        payload, user = cb.get("payload") or "", cb.get("user") or {}
        if payload.startswith("login:") and user.get("user_id"):
            name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
            ok = (await session.execute(text(f"""
                UPDATE max_logins
                   SET max_user_id = :u, max_name = :n, confirmed_at = now()
                 WHERE token = :t AND confirmed_at IS NULL AND used_at IS NULL
                   AND created_at > now() - interval '{MAX_TTL_MIN} minutes'"""),
                {"t": payload[6:], "u": user["user_id"], "n": name or None})).rowcount
            await session.commit()
            await max_call("POST", "/answers", params={"callback_id": cb.get("callback_id")},
                           json={"notification": "Готово — вернитесь на сайт"
                                 if ok else "Код устарел — начните вход заново"})

    return {"ok": True}
