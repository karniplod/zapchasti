"""Ошибки проверки полей — по-русски и с названием поля.

FastAPI на неверные данные отвечает 422 со списком на английском:
«Input should be greater than or equal to 0». Формы показывали это как
есть или как «[object Object]». Здесь тот же список переводится:

    {"detail": "Цена: не меньше 0",                      ← для всплывашки
     "errors": [{"field": "price", "msg": "не меньше 0"}]} ← к полям формы
"""

from fastapi.exceptions import RequestValidationError

# Названия полей так, как они подписаны в формах
LABELS = {
    "login": "Телефон или email", "password": "Пароль", "name": "Название",
    "phone": "Телефон", "email": "Email", "message": "Сообщение",
    "contact_name": "ФИО получателя", "contact_phone": "Телефон получателя",
    "delivery_address": "Адрес", "comment": "Комментарий",
    "vin": "VIN", "year": "Год", "color": "Цвет", "mileage_km": "Пробег",
    "plate": "Госномер", "purchase_price": "Цена закупки", "accepted_at": "Дата приёмки",
    "notes": "Заметки", "public_note": "Описание", "price": "Цена",
    "weight_kg": "Вес", "location": "Место хранения", "condition_note": "Дефекты",
    "oem_number": "Каталожный номер", "part_brand": "Бренд детали",
    "year_from": "Год начала", "year_to": "Год окончания", "query": "Запрос",
    "status": "Статус", "condition": "Состояние",
    "quantity": "Количество", "qty": "Количество",
}


def _text(e: dict) -> str:
    t, ctx = e.get("type", ""), e.get("ctx") or {}
    if t == "missing":
        return "обязательное поле"
    if t == "string_too_short":
        return f"не короче {ctx.get('min_length')} символов"
    if t == "string_too_long":
        return f"не длиннее {ctx.get('max_length')} символов"
    if t in ("greater_than_equal", "greater_than"):
        return f"не меньше {ctx.get('ge', ctx.get('gt'))}"
    if t in ("less_than_equal", "less_than"):
        return f"не больше {ctx.get('le', ctx.get('lt'))}"
    if t in ("int_parsing", "int_from_float", "float_parsing", "decimal_parsing",
             "decimal_type", "int_type", "float_type"):
        return "нужно число"
    if t.startswith("date") or t.startswith("datetime"):
        return "неверная дата"
    if t == "string_pattern_mismatch":
        return "неверный формат"
    if t in ("too_long", "too_short"):
        return "неверное количество значений"
    if t.startswith("bool"):
        return "нужно да или нет"
    if t == "value_error":
        return str(ctx.get("error") or "неверное значение")
    return "неверное значение"


def translate(exc: RequestValidationError) -> dict:
    errors = []
    for e in exc.errors():
        loc = [x for x in e.get("loc", ()) if x not in ("body", "query", "path", "form")]
        field = str(loc[-1]) if loc else ""
        errors.append({"field": field, "msg": _text(e)})
    first = errors[0] if errors else {"field": "", "msg": "неверные данные"}
    label = LABELS.get(first["field"])
    msg = first["msg"]
    if label and msg.lower().startswith(label.lower()):
        detail = msg            # текст из валидатора уже называет поле
    elif label:
        detail = f"{label}: {msg}"
    else:
        detail = f"Проверьте поля формы: {msg}"
    return {"detail": detail, "errors": errors}
