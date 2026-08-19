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
    app_name: str = "Разбор·Партс"
    debug: bool = False
    base_url: str = "https://example.ru"      # без слэша на конце, идёт в QR

    # --- база ---
    database_url: str
    db_echo: bool = False
    db_pool_size: int = 10

    # --- сессии ---
    secret_key: str                            # openssl rand -hex 32
    session_cookie: str = "razbor_session"
    session_ttl_hours: int = 12                # смена закончилась — вход заново

    # --- файлы ---
    media_root: Path = BASE_DIR / "media"
    static_root: Path = BASE_DIR / "static"
    max_upload_mb: int = 12
    image_max_side: int = 1600                 # больше для каталога не нужно
    thumb_side: int = 400

    # --- почта ---
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    order_notify_to: str = ""

    # --- магазин ---
    currency: str = "₽"
    reserve_hours: int = 48                    # сколько держим деталь под заказ

    @field_validator("base_url")
    @classmethod
    def strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
