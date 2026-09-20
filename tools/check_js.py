"""Синтаксис встроенных скриптов на страницах витрины.

    python tools/check_js.py        (сервер должен быть поднят)

Зачем: разметка и скрипты живут в одном шаблоне, и правка скрипта
через замену текста легко превращает
 внутри строки в настоящий
перенос. Для JavaScript это синтаксическая ошибка: молча перестаёт
работать весь блок, а внешне страница выглядит целой.

Нужен пакет esprima: pip install esprima. Он не знает catch без
переменной (ES2019), такие срабатывания отсеиваются.
"""
import http.cookiejar
import io
import json
import re
import sys
import urllib.error
import urllib.request

import esprima

B = "http://127.0.0.1:8100"
jar = http.cookiejar.CookieJar()
web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def call(path, data=None):
    req = urllib.request.Request(B + path, data=json.dumps(data).encode() if data else None)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with web.open(req) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        return e.read().decode()

out = io.StringIO()
PAGES = ["/", "/catalog", "/cart", "/contacts", "/delivery",
         "/account/login", "/nope"]
bad = 0
for p in PAGES:
    html = call(p)
    for n, src in enumerate(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S), 1):
        if not src.strip():
            continue
        try:
            esprima.parseScript(src)
        except Exception as e:
            m = re.search(r"Line (\d+)", str(e))
            line = src.split("\n")[int(m.group(1)) - 1].strip() if m else ""
            if re.search(r"\bcatch\s*\{", line):
                continue          # ES2019, парсер его не знает
            bad += 1
            print(f"  {p} скрипт {n}: {e}", file=out)
            print(f"      {line[:80]}", file=out)
print(f"страниц проверено: {len(PAGES)}, настоящих ошибок: {bad}", file=out)
print(out.getvalue())
sys.exit(1 if bad else 0)
