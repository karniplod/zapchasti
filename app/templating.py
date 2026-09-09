"""Общий движок шаблонов.

У каждого роутера был свой Jinja2Templates, и глобальные переменные,
записанные в один объект, не видел другой. Теперь объект один.
"""

from urllib.parse import unquote

from fastapi.templating import Jinja2Templates

from .config import settings

templates = Jinja2Templates(directory="templates")


def _file_version(name: str) -> str:
    """Версия файла = время правки: меняется файл — меняется адрес."""
    try:
        return str(int((settings.static_root / name).stat().st_mtime))
    except OSError:
        return "0"


def _css_version() -> str:
    return _file_version("site.css")


def _admin_css_version() -> str:
    return _file_version("admin.css")


def _chosen_city(request) -> str:
    """Город из куки — чтобы шапка отрисовалась сразу с нужным названием,
    а не мигнула заглушкой, пока грузится JS. Кириллица в куке лежит
    percent-encoded: браузер иначе её туда не пустит."""
    raw = request.cookies.get("city", "")
    return unquote(raw) if raw else ""


# Функции, а не значения: вызываются в шаблоне при каждой отрисовке,
# поэтому правка CSS видна без перезапуска
templates.env.globals["css_version"] = _css_version
templates.env.globals["admin_css_version"] = _admin_css_version
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["base_url"] = settings.base_url
templates.env.globals["chosen_city"] = _chosen_city
