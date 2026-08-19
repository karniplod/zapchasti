
#!/usr/bin/env python3
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from passlib.context import CryptContext
from app.config import settings
from app.database import Base

# Хеширование пароля
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

async def create_user():
    engine = create_async_engine(settings.database_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        # Здесь нужно импортировать модель User из вашего проекта
        # Если модель называется User, раскомментируйте:
        # from app.models import User
        
        # Пример создания пользователя (адаптируйте под вашу модель)
        hashed_password = pwd_context.hash("admin123")
        
        # Создаем пользователя
        # user = User(login="admin", password=hashed_password, role="admin")
        # session.add(user)
        # await session.commit()
        
        print("Пользователь создан!")
        print("Логин: admin")
        print("Пароль: admin123")

if __name__ == "__main__":
    asyncio.run(create_user())
