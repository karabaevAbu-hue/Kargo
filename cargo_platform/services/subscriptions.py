"""Проверка подписок раз в час (ТЗ, 3.4).

Одно правило для всех, в том числе после пробных 14 дней:
за 3 дня — напоминание; срок вышел — 3 льготных дня; потом пауза.
"""
import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from sqlalchemy import select

from cargo_platform.config import config
from cargo_platform.db.base import Session
from cargo_platform.db.models import Company, CompanyStatus
from cargo_platform.master_bot.common import as_utc, utcnow

log = logging.getLogger(__name__)


def next_status(status: CompanyStatus, paid_until, now) -> CompanyStatus:
    """Чистая функция — легко проверить тестом."""
    if status == CompanyStatus.blocked:
        return status
    if now <= paid_until:
        return status if status in (CompanyStatus.trial, CompanyStatus.active) else CompanyStatus.active
    if now <= paid_until + timedelta(days=config.grace_days):
        return CompanyStatus.grace
    return CompanyStatus.paused


async def check_once(master: Bot) -> None:
    now = utcnow()
    async with Session() as s:
        companies = (await s.execute(select(Company).where(Company.status != CompanyStatus.blocked))).scalars().all()
        notices: list[tuple[int, str]] = []
        for c in companies:
            until = as_utc(c.paid_until)
            new = next_status(c.status, until, now)
            key, text = None, None
            if new != c.status and new == CompanyStatus.grace:
                key = f"grace:{until:%Y%m%d}"
                text = (f"⚠️ Подписка закончилась {until:%d.%m.%Y}. Бот работает ещё {config.grace_days} дня "
                        "(льготный период), потом клиенты увидят «сервис временно недоступен». Данные сохранятся.")
            elif new != c.status and new == CompanyStatus.paused:
                key = f"paused:{until:%Y%m%d}"
                text = "⛔ Бот для клиентов приостановлен: подписка не оплачена. Данные хранятся 90 дней."
            elif new in (CompanyStatus.trial, CompanyStatus.active) and until - now <= timedelta(days=1):
                key = f"end:{until:%Y%m%d}"
                text = f"🔔 Подписка заканчивается сегодня-завтра ({until:%d.%m.%Y}). Продлите её в разделе «💳 Подписка»."
            elif new in (CompanyStatus.trial, CompanyStatus.active) and until - now <= timedelta(days=3):
                key = f"pre3:{until:%Y%m%d}"
                text = f"🔔 Подписка заканчивается {until:%d.%m.%Y}. Откройте «💳 Подписка», чтобы продлить."
            c.status = new
            if key and c.notice_key != key:
                c.notice_key = key
                notices.append((c.owner_tg_id, text))
        await s.commit()
    for tg_id, text in notices:
        try:
            await master.send_message(tg_id, text)
        except Exception as e:
            log.warning("notice %s: %s", tg_id, e)


async def loop(master: Bot) -> None:
    while True:
        try:
            await check_once(master)
        except Exception:
            log.exception("subscription check")
        await asyncio.sleep(3600)
