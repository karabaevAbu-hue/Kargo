"""Подключение к базе."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cargo_platform.config import config
from cargo_platform.db.models import Base

engine = create_async_engine(config.database_url, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Создаёт таблицы, если их нет. На проде дальше — миграции Alembic."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
