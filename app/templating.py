"""Общий движок шаблонов.

У каждого роутера был свой Jinja2Templates, и глобальные переменные,
записанные в один объект, не видел другой. Теперь объект один.
"""

from fastapi.templating import Jinja2Templates

from .config import settings

templates = Jinja2Templates(directory="templates")


def _css_version() -> str:
    """Версия стилей = время правки файла: меняется файл — меняется адрес."""
    try:
        return str(int((settings.static_root / "site.css").stat().st_mtime))
    except OSError:
        return "0"


templates.env.globals["css_version"] = _css_version()
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["base_url"] = settings.base_url
