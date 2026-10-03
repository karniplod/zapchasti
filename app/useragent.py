"""Устройство по строке User-Agent — коротко, для людей: «Chrome, Windows».

Нужна покупателю в окне кабинета («последний вход — с какого устройства»)
и менеджеру в карточке покупателя. Точность — «узнать своё устройство»,
не статистика: незнакомое честно называем «браузер».
"""

import re

# Порядок важен: Edge и Opera притворяются Chrome, Chrome — Safari
BROWSERS = [
    (r"YaBrowser/", "Яндекс Браузер"),
    (r"Edg(e|A|iOS)?/", "Edge"),
    (r"OPR/|Opera", "Opera"),
    (r"SamsungBrowser/", "Samsung Internet"),
    (r"Firefox/|FxiOS/", "Firefox"),
    (r"Chrome/|CriOS/", "Chrome"),
    (r"Safari/", "Safari"),
]
SYSTEMS = [
    (r"iPhone", "iPhone"),
    (r"iPad", "iPad"),
    (r"Android", "Android"),
    (r"Windows", "Windows"),
    (r"Mac OS X|Macintosh", "macOS"),
    (r"CrOS", "ChromeOS"),
    (r"Linux", "Linux"),
]


def device(ua: str | None) -> str:
    """'Mozilla/5.0 (Windows NT 10.0…) Chrome/126…' → 'Chrome, Windows'."""
    ua = ua or ""
    if not ua:
        return "неизвестное устройство"
    if "okhttp" in ua or "Dalvik" in ua:
        return "приложение, Android"
    browser = next((name for pat, name in BROWSERS if re.search(pat, ua)), "браузер")
    system = next((name for pat, name in SYSTEMS if re.search(pat, ua)), None)
    return f"{browser}, {system}" if system else browser
