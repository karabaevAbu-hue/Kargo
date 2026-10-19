"""Журнал действий менеджеров (ТЗ, 5.7). Из бота записи удалить нельзя."""
from sqlalchemy.ext.asyncio import AsyncSession

from cargo_platform.db.models import AuditLog


def log(s: AsyncSession, company_id: int, staff_tg_id: int, action: str,
        obj=None, old=None, new=None) -> None:
    s.add(AuditLog(company_id=company_id, staff_tg_id=staff_tg_id, action=action[:50],
                   object=(str(obj)[:100] if obj is not None else None),
                   old_value=None if old is None else str(old)[:1000],
                   new_value=None if new is None else str(new)[:1000]))
