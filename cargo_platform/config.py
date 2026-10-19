"""Настройки платформы. Все значения берутся из переменных окружения (.env)."""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Читает .env рядом с кодом или в текущей папке. Переменные окружения важнее файла."""
    for path in (Path(__file__).parent / ".env", Path.cwd() / ".env"):
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
            return


_load_dotenv()


def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


@dataclass(frozen=True)
class Config:
    # Токен ГЛАВНОГО бота платформы (из @BotFather)
    master_token: str = os.getenv("MASTER_BOT_TOKEN", "")
    # Публичный адрес сервера с HTTPS, без слеша в конце: https://cargo.example.kg
    base_url: str = os.getenv("BASE_URL", "").rstrip("/")
    # База: на старте SQLite, на сервере PostgreSQL
    #   sqlite+aiosqlite:///cargo.db
    #   postgresql+asyncpg://user:pass@db:5432/cargo
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///cargo.db")
    # Ключ шифрования токенов (Fernet). Создать: python -m cargo_platform.tools.genkey
    fernet_key: str = os.getenv("FERNET_KEY", "")
    # Секрет webhook главного бота
    master_secret: str = os.getenv("MASTER_WEBHOOK_SECRET", "change-me-master-secret")
    # Telegram ID владельца платформы (Абу). Через запятую, если несколько
    platform_admins: set[int] = field(default_factory=lambda: _ids(os.getenv("PLATFORM_ADMINS", "")))
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8080"))
    trial_days: int = int(os.getenv("TRIAL_DAYS", "14"))
    grace_days: int = int(os.getenv("GRACE_DAYS", "3"))
    support_username: str = os.getenv("SUPPORT_USERNAME", "")
    # Часовой пояс карго для отчёта дня и рассылок «на время»: Бишкек = UTC+6
    tz_offset_hours: int = int(os.getenv("TZ_OFFSET_HOURS", "6"))
    daily_report_hour: int = int(os.getenv("DAILY_REPORT_HOUR", "20"))


config = Config()
