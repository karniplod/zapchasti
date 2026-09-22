"""Проверка страниц витрины и админки: скрипты и встроенное.

    python tools/check_js.py        (сервер должен быть поднят)

Три проверки.

1. Встроенного быть не должно. Стили живут в static/css, скрипты —
   в static/js, данные для скриптов передаются data-атрибутами.
   Поэтому в отданной странице ошибка — любой <style>, встроенный
   <script>, атрибут style="" и обработчик вида onclick="".

2. Всё подключённое должно отдаваться. asset() при отсутствующем файле
   молча пишет v=0, и опечатка в пути видна только в консоли браузера:
   страница открывается, а скрипт не работает. Поэтому каждый
   <link rel=stylesheet> и <script src> со своего сервера запрашивается.

3. Синтаксис всех файлов static/js. Правка через замену текста легко
   превращает \\n внутри строки в настоящий перенос — для JavaScript это
   ошибка, и молча перестаёт работать весь файл. Там же ищется то, что
   не должно было уехать из шаблона: разметка Jinja ({{ }}, {% %}, {# #}),
   которую в статическом файле никто не обработает, и style="" в
   HTML-строках.

Админские страницы без входа отвечают редиректом на форму входа, и
проверялась бы форма, а не страница. Поэтому для них скрипт сам
подписывает сессию первого администратора — тем же ключом, что сервер.

Нужен пакет esprima: pip install esprima. Он знает язык только до
ES2017, а в коде есть catch без переменной, ?? и ?. — всё это браузеры
давно понимают. Такие места перед разбором переписываются в равносильный
старый синтаксис (см. downlevel): пропускать строку с ошибкой нельзя —
парсер останавливается на первой, и настоящая ошибка ниже осталась бы
незамеченной.
"""
import asyncio
import io
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import esprima

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.auth import signer  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import get_session  # noqa: E402

B = "http://127.0.0.1:8100"

STORE = ["/", "/catalog", "/cart", "/contacts", "/delivery", "/account/login", "/nope",
         "/login"]
ADMIN = ["/admin", "/reports", "/api/reference/review/page", "/orders", "/parts",
         "/donors", "/intake", "/stock/new"]

# Что считается встроенным в отданной странице
INLINE = [
    ("<style>", re.compile(r"<style[\s>]", re.I)),
    ("встроенный <script>",
     re.compile(r"<script(?![^>]*\bsrc=)(?![^>]*application/(?:ld\+)?json)[^>]*>", re.I)),
    ('style=""', re.compile(r"<[a-z][^>]*\sstyle\s*=", re.I)),
    ("обработчик on*=", re.compile(r"<[a-z][^>]*\son[a-z]+\s*=", re.I)),
]
ASSET = re.compile(r'<(?:script[^>]*\ssrc|link[^>]*\shref)="(/static/[^"]+)"', re.I)


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
    # errors="replace": подключённым бывает и двоичный файл (шрифт),
    # а для него важен только код ответа
    req = urllib.request.Request(B + path)
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def downlevel(src: str) -> str:
    """Современный синтаксис -> ES2017 для парсера. Смысл кода не
    важен, важна только грамматика: a ?? b и a || b разбираются
    одинаково, поэтому замена ничего не прячет."""
    src = re.sub(r"\bcatch\s*\{", "catch (_e) {", src)
    src = src.replace("??", "||")
    src = re.sub(r"\?\.(?=[\[(])", "", src)             # a?.[0], f?.()
    src = re.sub(r"\?\.(?=[A-Za-z_$])", ".", src)        # a?.b
    return src


def parse(name, src, out) -> int:
    try:
        esprima.parseScript(downlevel(src))
        return 0
    except Exception as e:
        m = re.search(r"Line (\d+)", str(e))
        line = src.split("\n")[int(m.group(1)) - 1].strip() if m else ""
        print(f"  {name}: {e}", file=out)
        print(f"      {line[:80]}", file=out)
        return 1


def inline_in(path, html, out) -> int:
    bad = 0
    for what, rx in INLINE:
        hits = rx.findall(html)
        if hits:
            bad += len(hits)
            print(f"  {path}: встроено — {what} ×{len(hits)}: {hits[0][:70]}", file=out)
    return bad


def main():
    out = io.StringIO()
    admin, donor, sku = asyncio.run(fixtures())

    pages = [(p, None) for p in STORE]
    if sku:
        pages.append((f"/p/{sku}", None))
    if admin:
        cookie = f"{settings.session_cookie}={signer.dumps({'uid': admin.id, 'role': admin.role})}"
        extra = [f"/donors/{donor}/dismantle", f"/donors/{donor}/labels"] if donor else []
        pages += [(p, cookie) for p in ADMIN + extra]
    else:
        print("  администратора в базе нет — админские страницы не проверены", file=out)

    bad = 0
    assets = set()
    for path, cookie in pages:
        status, html = fetch(path, cookie)
        # Админская страница, которая не открылась, — это не «ошибок нет»,
        # а «не проверено»: об этом надо сказать, а не промолчать
        if cookie and status != 200:
            bad += 1
            print(f"  {path}: ответ {status}, страница не проверена", file=out)
            continue
        bad += inline_in(path, html, out)
        assets.update(ASSET.findall(html))

    # Всё подключённое отдаётся
    for url in sorted(assets):
        status, _ = fetch(url)
        if status != 200:
            bad += 1
            print(f"  {url}: ответ {status} — файл подключён, но не отдаётся", file=out)

    # Синтаксис и остатки шаблона во всех файлах static/js
    files = sorted((ROOT / "static" / "js").rglob("*.js"))
    for f in files:
        src = f.read_text(encoding="utf-8")
        name = f.relative_to(ROOT).as_posix()
        bad += parse(name, src, out)
        for what, rx in [("разметка Jinja", re.compile(r"\{\{|\{%|\{#")),
                         ('style="" в HTML-строке', re.compile(r"\sstyle\s*=\s*[\"'\\]"))]:
            n = len(rx.findall(src))
            if n:
                bad += n
                print(f"  {name}: {what} ×{n}", file=out)

    print(f"страниц: {len(pages)}, подключённых файлов: {len(assets)}, "
          f"файлов JS: {len(files)}, ошибок: {bad}", file=out)
    print(out.getvalue())
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
