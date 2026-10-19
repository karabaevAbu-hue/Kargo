"""Подготовка чистой базы для тестов (без побочных эффектов при импорте)."""


async def fresh_db() -> None:
    """Чистая база + миграции Alembic (так же, как на сервере). На PostgreSQL схема пересоздаётся целиком."""
    from sqlalchemy import text
    from cargo_platform.db import migrate
    from cargo_platform.db.base import engine
    if engine.dialect.name == "postgresql":
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    await migrate.upgrade()
