"""Люди: телефон, email, логин, имя, ФИО, пароль.

В браузере те же правила — Check.phone, email, login, name, fio,
newPassword в static/js/validation/rules.js.
"""

import re


def normalize_phone(raw: str) -> str | None:
    """К одному виду: +79123456789.

    Один и тот же человек напишет 8 912…, +7 912… и 7(912)… — без
    приведения в таблице окажется три покупателя с одним телефоном,
    а UNIQUE на phone этого не заметит.
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return None
    return "+" + digits


# Простая проверка формы: «что-то@что-то.зона». Полная по RFC пропускает
# такое, что ни один почтовик не примет, а настоящая проверка — письмо
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def normalize_email(raw: str) -> str | None:
    v = (raw or "").strip().lower()
    return v if len(v) <= 200 and EMAIL_RE.match(v) else None


def parse_login(raw: str) -> tuple[str, str] | None:
    """Что ввели в поле «Телефон или email»: есть @ — почта, иначе телефон."""
    if "@" in (raw or ""):
        v = normalize_email(raw)
        return ("email", v) if v else None
    v = normalize_phone(raw)
    return ("phone", v) if v else None


# Имя человека: с буквы, дальше буквы, пробел, дефис, апостроф, точка.
# Цифры и подчёркивание \w тоже пропустил бы — их вычитаем отдельно
NAME_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ .'’-]){0,79}$")


def check_name(raw: str | None, optional: bool = True) -> str | None:
    """Имя в заявке или при регистрации — как человек себя назвал."""
    v = (raw or "").strip()
    if not v:
        return None if optional else "Как к вам обращаться?"
    return None if NAME_RE.match(v) else "В имени — только буквы, пробел и дефис"


# ФИО получателя заказа: каждое слово — буквы, внутри дефис или апостроф
# (Иванова-Петрова, д’Артаньян). Частицы отчества пишутся со строчной
FIO_WORD_RE = re.compile(r"^[^\W\d_]+(?:[-'’][^\W\d_]+)*$")
FIO_LOWER = {"оглы", "кызы", "улы", "гызы", "уулу"}


def check_fio(raw: str, no_patronymic: bool = False) -> tuple[str | None, str | None]:
    """ФИО получателя заказа → (ФИО с заглавных букв, текст ошибки).
    Нужны фамилия, имя и отчество; «нет отчества» — фамилия и имя.
    Без инициалов: по «Иванов И. И.» посылку в пункте выдачи не отдадут."""
    words = (raw or "").split()
    need = "фамилию и имя" if no_patronymic else "фамилию, имя и отчество"
    if not words:
        return None, f"Укажите {need}"
    if len(" ".join(words)) > 80:
        return None, "Слишком длинно — до 80 символов"
    for w in words:
        if "." in w or len(w.strip("-'’")) < 2:
            return None, "Полностью, без инициалов: Иванов Иван Иванович"
        if not FIO_WORD_RE.match(w):
            return None, "Только буквы, пробел и дефис"
    if len(words) > 5:
        return None, "Фамилия, имя и отчество — не больше пяти слов"
    if len(words) < (2 if no_patronymic else 3):
        if len(words) == 2:
            return None, "Добавьте отчество — или отметьте «Нет отчества»"
        return None, f"Нужны {need.replace('фамилию', 'фамилия')} — через пробел"
    fixed = [w if w.lower() in FIO_LOWER else
             "-".join(p[:1].upper() + p[1:] for p in w.split("-")) for w in words]
    return " ".join(fixed), None


def recipient(raw: str, no_patronymic: bool = False) -> tuple[str | None, str | None]:
    """ФИО получателя с подписью поля в тексте ошибки — для ответа 422."""
    name, err = check_fio(raw, no_patronymic)
    return name, ("ФИО получателя: " + err[0].lower() + err[1:]) if err else None


def check_new_password(pw: str) -> str | None:
    """Пароль при регистрации: из одних цифр подбирается за минуты."""
    if not (any(c.isalpha() for c in pw) and any(c.isdigit() for c in pw)):
        return "В пароле нужны и буквы, и цифры"
    return None
