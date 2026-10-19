"""Удаление данных ушедшего карго через 90 дней (ТЗ, раздел 9). Только вручную, командой /purge."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete

from cargo_platform.db.base import Session
from cargo_platform.db.models import (
    AuditLog, Batch, Broadcast, Charge, Client, Company, CompanyBot, CompanySettings, CompanyStatus,
    ErrorLog, ExcelTemplate, Message, NoticeLog, Parcel, Payment, Staff, StaffInvite, Subscription, Track,
)
from cargo_platform.master_bot.common import as_utc, utcnow

RETENTION_DAYS = 90

# порядок важен: сначала зависимые таблицы
_TABLES = [NoticeLog, AuditLog, Broadcast, Message, Payment, Charge, Parcel, Track, Batch, ExcelTemplate,
           Client, StaffInvite, Staff, Subscription, CompanySettings, CompanyBot, ErrorLog]


def can_purge(company: Company) -> str | None:
    """None — можно удалять. Иначе текст, почему нельзя."""
    if company.status not in (CompanyStatus.paused, CompanyStatus.blocked):
        return "Компания работает. Сначала заблокируйте её (/block) или дождитесь паузы за неоплату."
    age = utcnow() - as_utc(company.paid_until)
    if age < timedelta(days=RETENTION_DAYS):
        return f"Данные хранятся {RETENTION_DAYS} дней после окончания подписки. Прошло {age.days}."
    return None


async def purge(company_id: int) -> None:
    async with Session() as s:
        for model in _TABLES:
            await s.execute(delete(model).where(model.company_id == company_id))
        await s.execute(delete(Company).where(Company.id == company_id))
        await s.commit()
