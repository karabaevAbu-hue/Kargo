"""Подключение к базе. Схему создают только миграции Alembic (db/migrate.py)."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cargo_platform.config import config

engine = create_async_engine(config.database_url, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def check_schema() -> None:
    """При старте сервера: убедиться, что миграции применены."""
    from cargo_platform.db.migrate import ensure_current
    await ensure_current(engine)
