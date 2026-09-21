"""Синтаксис встроенных скриптов на страницах витрины и админки.

    python tools/check_js.py        (сервер должен быть поднят)

Зачем: разметка и скрипты живут в одном шаблоне, и правка скрипта
через замену текста легко превращает \\n внутри строки в настоящий
перенос. Для JavaScript это синтаксическая ошибка: молча перестаёт
работать весь блок, а внешне страница выглядит целой.

Админские страницы без входа отвечают редиректом на форму входа, и
проверялась бы форма, а не страница. Поэтому для них скрипт сам
подписывает сессию первого администратора — тем же ключом, что сервер.

Нужен пакет esprima: pip install esprima. Он знает язык только до
ES2017, а в шаблонах есть catch без переменной, ?? и ?. — всё это
браузеры давно понимают. Такие места перед разбором переписываются
в равносильный старый синтаксис (см. downlevel): пропускать строку
с ошибкой нельзя — парсер останавливается на первой, и настоящая
ошибка ниже по скрипту осталась бы незамеченной.
"""
import asyncio
import io
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import esprima

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.auth import signer  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import get_session  # noqa: E402

B = "http://127.0.0.1:8100"

STORE = ["/", "/catalog", "/cart", "/contacts", "/delivery", "/account/login", "/nope",
         "/login"]
ADMIN = ["/admin", "/reports", "/api/reference/review/page", "/orders", "/parts",
         "/donors", "/intake", "/stock/new"]


async def fixtures():
    """Кем войти и какие страницы с номером в адресе взять: машину
    для страницы разбора и деталь для карточки на витрине."""
    agen = get_session()
    s = await agen.__anext__()
    try:
        admin = (await s.execute(text(
            "SELECT id, role FROM users WHERE role = 'admin' ORDER BY id LIMIT 1"))).first()
        donor = (await s.execute(text(
            "SELECT id FROM donors ORDER BY id DESC LIMIT 1"))).scalar()
        sku = (await s.execute(text(
            "SELECT sku FROM parts WHERE status = 'in_stock' AND published "
            "ORDER BY id DESC LIMIT 1"))).scalar()
        return admin, donor, sku
    finally:
        await agen.aclose()


def fetch(path, cookie=None):
    req = urllib.request.Request(B + path)
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def downlevel(src: str) -> str:
    """Современный синтаксис -> ES2017 для парсера. Смысл кода не
    важен, важна только грамматика: a ?? b и a || b разбираются
    одинаково, поэтому замена ничего не прячет."""
    src = re.sub(r"\bcatch\s*\{", "catch (_e) {", src)
    src = src.replace("??", "||")
    src = re.sub(r"\?\.(?=[\[(])", "", src)             # a?.[0], f?.()
    src = re.sub(r"\?\.(?=[A-Za-z_$])", ".", src)        # a?.b
    return src


def check(path, html, out):
    bad = 0
    for n, src in enumerate(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S), 1):
        if not src.strip():
            continue
        try:
            esprima.parseScript(downlevel(src))
        except Exception as e:
            m = re.search(r"Line (\d+)", str(e))
            line = src.split("\n")[int(m.group(1)) - 1].strip() if m else ""
            bad += 1
            print(f"  {path} скрипт {n}: {e}", file=out)
            print(f"      {line[:80]}", file=out)
    return bad


def main():
    out = io.StringIO()
    admin, donor, sku = asyncio.run(fixtures())

    pages = [(p, None) for p in STORE]
    if sku:
        pages.append((f"/p/{sku}", None))

    if admin:
        cookie = f"{settings.session_cookie}={signer.dumps({'uid': admin.id, 'role': admin.role})}"
        admin_pages = ADMIN + ([f"/donors/{donor}/dismantle"] if donor else [])
        pages += [(p, cookie) for p in admin_pages]
    else:
        print("  администратора в базе нет — админские страницы не проверены", file=out)

    bad = 0
    for path, cookie in pages:
        status, html = fetch(path, cookie)
        # Админская страница, которая не открылась, — это не «ошибок нет»,
        # а «не проверено»: об этом надо сказать, а не промолчать
        if cookie and status != 200:
            bad += 1
            print(f"  {path}: ответ {status}, скрипты не проверены", file=out)
            continue
        bad += check(path, html, out)

    print(f"страниц проверено: {len(pages)}, настоящих ошибок: {bad}", file=out)
    print(out.getvalue())
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
