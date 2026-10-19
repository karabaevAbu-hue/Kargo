"""Для живого теста в одиночку: добавляет тестовых клиентов БЕЗ Telegram, чтобы Excel находил коды.

    python -m cargo_platform.tools.dev_seed "Название вашего карго" 2

Клиенты получают следующие свободные коды (например TST-0002, TST-0003). Сообщений им бот не шлёт
(нет Telegram), но начисления, долги и выдача работают как у обычных клиентов.
"""
import asyncio
import sys

from sqlalchemy import select

from cargo_platform.db.base import Session
from cargo_platform.db.models import Client, Company
from cargo_platform.services.codes import issue_code

NAMES = ["Айбек Тестов", "Нурия Тестова", "Бакыт Тестов", "Айгуль Тестова", "Эрлан Тестов"]


async def seed(name: str, count: int) -> list[str]:
    async with Session() as s:
        company = await s.scalar(select(Company).where(Company.name == name))
        if not company:
            raise SystemExit(f"Карго «{name}» не найдено. Проверьте название (как при регистрации).")
        n_existing = len((await s.execute(select(Client).where(Client.company_id == company.id))).scalars().all())
        codes = []
        for i in range(count):
            code = await issue_code(s, company.id)
            s.add(Client(company_id=company.id, code=code, full_name=NAMES[i % len(NAMES)],
                         phone=f"+99670000{n_existing + i + 1:04d}", lang="ru"))
            codes.append(code)
        await s.commit()
    return codes


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit('Формат: python -m cargo_platform.tools.dev_seed "Название карго" КОЛИЧЕСТВО')
    codes = asyncio.run(seed(sys.argv[1], int(sys.argv[2])))
    print("Добавлены клиенты:", ", ".join(codes))


if __name__ == "__main__":
    main()
