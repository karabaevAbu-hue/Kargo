"""Создаёт или обновляет файл .env, чтобы ничего не править руками.

    python -m cargo_platform.tools.make_env                 # спросит всё нужное
    python -m cargo_platform.tools.make_env --base-url https://xxxx.trycloudflare.com   # только сменить адрес

Ключ шифрования (FERNET_KEY) создаётся один раз и НИКОГДА не перезаписывается:
без него токены ботов из базы не расшифровать.
"""
import argparse
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

ENV = Path(__file__).resolve().parent.parent / ".env"
LOCAL_DB = "postgresql+asyncpg://cargo:cargo@localhost:5432/cargo"


def read() -> dict[str, str]:
    data: dict[str, str] = {}
    if ENV.is_file():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    return data


def ask(label: str, current: str = "", required: bool = True) -> str:
    hint = f" [{current}]" if current else ""
    while True:
        value = input(f"{label}{hint}: ").strip() or current
        if value or not required:
            return value
        print("  Это поле обязательно.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url")
    ap.add_argument("--sqlite", action="store_true", help="использовать SQLite вместо PostgreSQL (без Docker)")
    args = ap.parse_args()
    env = read()
    env.setdefault("FERNET_KEY", Fernet.generate_key().decode())
    env.setdefault("MASTER_WEBHOOK_SECRET", secrets.token_urlsafe(32))
    env.setdefault("DATABASE_URL", "sqlite+aiosqlite:///cargo.db" if args.sqlite else LOCAL_DB)
    if args.sqlite:
        env["DATABASE_URL"] = "sqlite+aiosqlite:///cargo.db"
    env.setdefault("TZ_OFFSET_HOURS", "6")
    env.setdefault("DAILY_REPORT_HOUR", "20")

    if args.base_url:
        env["BASE_URL"] = args.base_url.rstrip("/")
    else:
        print("Нужно 4 значения. Токен главного бота выдаёт @BotFather, свой Telegram ID показывает @userinfobot.")
        env["MASTER_BOT_TOKEN"] = ask("Токен ГЛАВНОГО бота платформы", env.get("MASTER_BOT_TOKEN", ""))
        env["PLATFORM_ADMINS"] = ask("Ваш Telegram ID (только цифры)", env.get("PLATFORM_ADMINS", ""))
        env["BASE_URL"] = ask("Адрес туннеля https://....trycloudflare.com", env.get("BASE_URL", "")).rstrip("/")
        env["SUPPORT_USERNAME"] = ask("Ваш username для кнопки «Поддержка» (без @, можно пусто)",
                                      env.get("SUPPORT_USERNAME", ""), required=False)
    ENV.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n", encoding="utf-8")
    ENV.chmod(0o600)
    print(f"\nГотово: {ENV}\nБаза: {env['DATABASE_URL'].split('@')[-1]}\nАдрес: {env.get('BASE_URL', '—')}")


if __name__ == "__main__":
    main()
