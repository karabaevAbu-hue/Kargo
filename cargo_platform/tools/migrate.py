"""python -m cargo_platform.tools.migrate — создаёт и обновляет таблицы по миграциям Alembic."""
from cargo_platform.config import config
from cargo_platform.db import migrate


def main() -> None:
    shown = config.database_url.split("@")[-1] if "@" in config.database_url else config.database_url
    print(f"Обновляю базу: {shown}")
    migrate.upgrade_sync()
    print("Готово: схема базы актуальна.")


if __name__ == "__main__":
    main()
