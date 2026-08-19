"""Подключение к базе.

Роутеры работают через text() и SQL напрямую — схема уже описана в sql/,
дублировать её в ORM-моделях смысла нет, а рекурсивные запросы по дереву
категорий и применимости в ORM всё равно не выражаются.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings

engine = create_async_engine(
    str(settings.database_url).replace("postgresql://", "postgresql+asyncpg://"),
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=5,
    pool_pre_ping=True,  # переживает ночной обрыв соединения
    pool_recycle=1800,
)

SessionFactory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Зависимость FastAPI. Откат при любой ошибке — иначе битая транзакция
    останется в пуле и отравит следующий запрос."""
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_connection() -> bool:
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose() -> None:
    await engine.dispose()
