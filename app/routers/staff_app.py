"""Приложение сотрудника для Android: установка и обновления без Google Play.

Сборку кладут в media/app/: avtodonor.apk и version.json рядом
({"version_code": 2, "version_name": "1.1", "notes": "что нового"}).
Caddy отдаёт сам файл, отсюда — страница для первой установки и номер
свежей версии: приложение сравнивает его со своим и предлагает обновиться.
"""

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..config import settings
from ..templating import templates

router = APIRouter(tags=["staff-app"])

APK_URL = "/media/app/avtodonor.apk"


def current_release() -> dict | None:
    """Что выложено. None — сборки ещё нет или version.json битый."""
    folder = settings.media_root / "app"
    if not (folder / "avtodonor.apk").exists():
        return None
    try:
        meta = json.loads((folder / "version.json").read_text(encoding="utf-8-sig"))
        return {
            "version_code": int(meta["version_code"]),
            "version_name": str(meta["version_name"]),
            "notes": str(meta.get("notes") or ""),
            "url": APK_URL,
        }
    except (OSError, ValueError, KeyError):
        return None


@router.get("/api/app/version")
async def app_version():
    """Без входа: приложение спрашивает и до логина, а секрета в номере нет.
    Сборки нет — version_code 0, и приложение молчит."""
    return current_release() or {"version_code": 0, "version_name": "", "notes": "", "url": None}


@router.get("/app", response_class=HTMLResponse)
async def app_page(request: Request):
    """Открыть на телефоне сотрудника, скачать, поставить. Ссылки на неё
    с витрины нет — её дают сотрудникам."""
    return templates.TemplateResponse(
        "staff_app.html", {"request": request, "release": current_release()}
    )
