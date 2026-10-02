"""Настройки. Всё, что отличается между машиной разработчика и сервером,
живёт в .env и никогда не попадает в git."""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- основное ---
    app_name: str = "Автодонор"
    debug: bool = False
    base_url: str = "https://example.ru"  # без слэша на конце, идёт в QR

    # --- база ---
    database_url: str
    db_echo: bool = False
    db_pool_size: int = 10

    # --- сессии ---
    secret_key: str  # openssl rand -hex 32
    session_cookie: str = "razbor_session"
    session_ttl_hours: int = 12  # смена закончилась — вход заново
    # Токен Android-приложения живёт дольше куки: телефон закреплён за
    # человеком, а роль и is_active всё равно перечитываются на каждом запросе
    app_token_ttl_days: int = 30

    # --- файлы ---
    # База GeoIP для подсказки города в каталоге. Файла может не быть —
    # тогда определение просто выключено (см. app/services/geo.py)
    geoip_db: Path = BASE_DIR / "data" / "GeoLite2-City.mmdb"

    media_root: Path = BASE_DIR / "media"
    static_root: Path = BASE_DIR / "static"
    max_upload_mb: int = 12
    image_max_side: int = 1600  # больше для каталога не нужно
    thumb_side: int = 400

    # --- почта ---
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    order_notify_to: str = ""
    # Адрес отправителя; пусто — тот же, что SMTP_USER. Почтовики
    # (Яндекс, Mail.ru) отправляют только от адреса самого ящика
    mail_from: str = ""

    # --- внешние источники ---
    # Парсеры выключены по умолчанию: поднятый где-то стенд не должен
    # начать ходить на чужие сайты сам по себе
    parsers_enabled: bool = False

    # Поисковик для подсказки каталожного номера.
    # brave — Brave Search API: нужен ключ, отвечает JSON
    # duckduckgo — без ключа, но глушит по IP после десятка запросов;
    #          годится посмотреть, не годится для работы
    search_provider: str = "duckduckgo"
    search_api_key: str = ""

    # Потолок запросов в сутки. У Brave каждый месяц бесплатно $5 —
    # это 1000 запросов, дальше $5 за тысячу, и с привязанной картой
    # «дальше» наступает молча. Упёрлись в потолок: подсказка замолкает,
    # приёмка работает как была
    search_daily_limit: int = 100

    # --- магазин ---
    currency: str = "₽"
    reserve_hours: int = 48  # сколько держим деталь под заказ

    # --- быстрый вход покупателя ---
    # Пустое значение — кнопки этого входа на сайте нет. Адрес возврата
    # у всех один вид: {base_url}/auth/<провайдер>/callback
    google_client_id: str = ""
    google_client_secret: str = ""
    # VK ID (id.vk.com): OAuth 2.1 с PKCE, секрет не нужен — только ID приложения
    vk_client_id: str = ""
    # Яндекс ID (oauth.yandex.ru): почта и телефон приходят подтверждёнными
    yandex_client_id: str = ""
    yandex_client_secret: str = ""
    # Telegram Login Widget: бот из @BotFather, домен сайта задаётся там же /setdomain
    telegram_bot_name: str = ""
    telegram_bot_token: str = ""
    # MAX: OAuth нет, вход через бота. Вебхук бота — {base_url}/auth/max/webhook,
    # секрет — тот же, что передан при подписке (заголовок X-Max-Bot-Api-Secret)
    max_bot_name: str = ""
    max_bot_token: str = ""
    max_webhook_secret: str = ""

    # --- доставка службами ---
    # СДЭК: ключи интеграции из личного кабинета (по договору). CDEK_TEST=true —
    # учебная среда api.edu.cdek.ru с общими тестовыми ключами: цены и пункты
    # выдачи настоящие по виду, отправить по ним ничего нельзя
    cdek_client_id: str = ""
    cdek_client_secret: str = ""
    cdek_test: bool = False
    # Яндекс Доставка (межгород): Bearer-токен из кабинета. Склад отгрузки
    # (platform_station_id) — у каждого филиала свой, branches.yandex_station_id.
    # Тестовая среда работает только по Москве; её токен — в документации
    # Яндекса («Доступ к API»), склад — station_id ниже или тестовый сам
    yandex_delivery_token: str = ""
    yandex_delivery_station_id: str = ""
    yandex_delivery_test: bool = False
    # Почта России: публичный тарификатор tariff.pochta.ru — ключ не нужен
    pochta_enabled: bool = True
    # DaData «Подсказки» (dadata.ru, бесплатно до 10 000 запросов в день):
    # улица и дом по мере ввода, индекс по полному адресу. Пусто — адрес
    # вводят руками, индекс тоже
    dadata_api_key: str = ""

    # --- онлайн-оплата ---
    # ЮKassa: shopId и секретный ключ из личного кабинета. Уведомления —
    # {base_url}/api/payments/yookassa. Пусто — платят при получении
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    # Чеки по 54-ФЗ: true, если в ЮKassa подключены «Чеки от ЮKassa» или своя
    # онлайн-касса — тогда без данных чека платёж отклоняется
    yookassa_receipts: bool = False
    # Ставка НДС в чеке (коды ЮKassa): 1 — без НДС (УСН, самозанятые),
    # 2 — 0%, 3 — 10%, 4 — 20%; код ставки 22% (с 2026 года) — по справочнику
    # ЮKassa «Ставки НДС», он там добавлен отдельным номером
    yookassa_vat_code: int = 1
    # Система налогообложения 1–6; нужна, только если их у магазина несколько
    yookassa_tax_system_code: int | None = None
    # Робокасса (robokassa.com): логин магазина и пароли №1 и №2 из
    # «Технических настроек». ResultURL — {base_url}/api/payments/robokassa/result
    # (POST), Success/Fail — {base_url}/pay/robokassa/success и /fail (GET),
    # алгоритм подписи — MD5. ROBOKASSA_TEST=true — тестовые платежи
    robokassa_login: str = ""
    robokassa_password1: str = ""
    robokassa_password2: str = ""
    robokassa_test: bool = False
    # Показывать «Робокассу» покупателю, пока ключей нет: заказ
    # оформляется, а оплатить предлагаем при получении
    robokassa_preview: bool = True
    # Учебная оплата без банка: страница с кнопками «оплатить / отказаться».
    # Только для проверки на стенде — на рабочем сайте не включать
    payment_demo: bool = False

    @field_validator("base_url")
    @classmethod
    def strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("geoip_db")
    @classmethod
    def abs_geoip(cls, v: Path) -> Path:
        # В .env путь удобнее писать относительным — считаем его от корня
        # проекта, а не от текущего каталога запуска
        return v if v.is_absolute() else BASE_DIR / v

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
