"""Окружение Alembic: асинхронное подключение, адрес базы из настроек платформы."""
import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from cargo_platform.config import config as app_config
from cargo_platform.db.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    # tools/migrate.py и тесты могут передать адрес явно, иначе — из .env
    return config.get_main_option("sqlalchemy.url") or app_config.database_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      compare_type=True, render_as_batch=_url().startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True,
                      render_as_batch=connection.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
