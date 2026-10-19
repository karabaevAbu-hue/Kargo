"""Миграции Alembic: схема после upgrade совпадает с моделями, откат и повторное применение работают,
сервер отказывается стартовать на базе без миграций.

    python -m cargo_platform.tests.test_migrations
"""
import asyncio
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update({
    "MASTER_BOT_TOKEN": "111111:" + "M" * 35, "BASE_URL": "https://test.example",
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{_tmp}/m.db",
    "FERNET_KEY": "pXlyrCqA2ZcOfnS7yp2c9N2VjfV5lW3l8Vh3X0bHk0c=",
})

from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

from cargo_platform.db import migrate  # noqa: E402
from cargo_platform.db.base import check_schema, engine  # noqa: E402
from cargo_platform.db.models import Base  # noqa: E402
from cargo_platform.tests.dbutil import fresh_db  # noqa: E402

OK = 0


def check(cond, name):
    global OK
    if not cond:
        print("❌", name)
        sys.exit(1)
    OK += 1
    print("✅", name)


async def wipe():
    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        else:
            await conn.run_sync(lambda c: Base.metadata.drop_all(c))
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


async def main():
    await wipe()
    try:
        await check_schema()
        check(False, "пустая база должна отклоняться")
    except SystemExit as e:
        check("tools.migrate" in str(e), "сервер не стартует на базе без миграций и подсказывает команду")
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    check("companies" not in tables, "таблицы сами не создаются")

    await fresh_db()
    await check_schema()
    check(True, "после миграции проверка схемы проходит")
    async with engine.connect() as conn:
        diff = await conn.run_sync(lambda c: compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata))
    check(not diff, f"миграции совпадают с моделями (расхождений нет){'' if not diff else ': ' + str(diff)[:300]}")

    await asyncio.to_thread(lambda: migrate.command.downgrade(migrate.alembic_config(), "base"))
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    check(tables == ["alembic_version"] or not tables, "откат миграции убирает все таблицы")
    await migrate.upgrade()
    await check_schema()
    check(True, "повторное применение миграции работает")
    print(f"\nВсе проверки пройдены: {OK}")


if __name__ == "__main__":
    asyncio.run(main())
