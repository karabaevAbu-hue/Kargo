"""Миграции базы (Alembic). Таблицы автоматически НЕ создаются: схему ведут только миграции.

    python -m cargo_platform.tools.migrate          # привести базу к последней версии
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from cargo_platform.config import config as app_config

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", (url or app_config.database_url).replace("%", "%%"))
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def upgrade_sync(url: str | None = None, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


async def upgrade(url: str | None = None, revision: str = "head") -> None:
    # alembic внутри сам запускает цикл событий, поэтому из async-кода — в отдельном потоке
    await asyncio.to_thread(upgrade_sync, url, revision)


async def ensure_current(engine) -> None:
    """Сервер не стартует на базе без миграций или с устаревшей схемой: понятное сообщение вместо падения посреди работы."""
    async with engine.connect() as conn:
        current = await conn.run_sync(lambda c: MigrationContext.configure(c).get_current_revision())
    head = head_revision()
    if current != head:
        raise SystemExit(
            f"База не обновлена (версия схемы: {current or 'пусто'}, нужна: {head}).\n"
            "Выполните: python -m cargo_platform.tools.migrate"
        )
