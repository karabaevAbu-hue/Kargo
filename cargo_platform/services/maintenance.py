"""Фоновые задачи: запланированные рассылки, напоминания клиентам, платное хранение, отчёт дня.

Одна петля раз в минуту. Каждое напоминание пишется в notice_log, поэтому не уйдёт дважды,
даже если сервер перезапустится.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from cargo_platform.admin_bot.broadcast import spawn
from cargo_platform.admin_bot.panel import debtors
from cargo_platform.client_bot.texts import ct
from cargo_platform.config import config
from cargo_platform.db.base import Session
from cargo_platform.db.models import (
    Broadcast, Client, Company, CompanySettings, CompanyStatus, NoticeLog, Parcel,
)
from cargo_platform.master_bot.common import as_utc, utcnow
from cargo_platform.services import notify, reports
from cargo_platform.services.billing import fmt_money, fmt_price
from cargo_platform.services.parcels import AT_PVZ, update_storage

log = logging.getLogger(__name__)


async def once(company_id: int, client_id: int, key: str) -> bool:
    """True — ключ записан впервые (можно отправлять); False — уже отправляли."""
    async with Session() as s:
        s.add(NoticeLog(company_id=company_id, client_id=client_id, key=key))
        try:
            await s.commit()
        except IntegrityError:
            return False
    return True


async def resume_broadcasts(manager) -> None:
    """После перезапуска сервера незавершённые рассылки продолжаются (ТЗ, раздел 9)."""
    async with Session() as s:
        rows = (await s.execute(select(Broadcast).where(Broadcast.status == "sending"))).scalars().all()
    for b in rows:
        bot = manager.get_bot(b.company_id)
        if bot:
            spawn(bot, b.company_id, b.id)


async def run_scheduled_broadcasts(manager, now) -> int:
    async with Session() as s:
        due = (await s.execute(select(Broadcast).where(Broadcast.status == "scheduled",
                                                       Broadcast.scheduled_at <= now))).scalars().all()
    started = 0
    for b in due:
        async with Session() as s:      # атомарно «scheduled → sending»: два тика не запустят рассылку дважды
            res = await s.execute(update(Broadcast).where(Broadcast.id == b.id, Broadcast.status == "scheduled")
                                  .values(status="sending"))
            await s.commit()
        bot = manager.get_bot(b.company_id)
        if res.rowcount == 1 and bot:
            spawn(bot, b.company_id, b.id)
            started += 1
    return started


async def client_reminders(manager, company: Company, st: CompanySettings, now) -> None:
    bot = manager.get_bot(company.id)
    if not bot:
        return
    # напоминание об оплате: через 2 и 5 дней после прибытия
    async with Session() as s:
        firsts = dict((await s.execute(select(Parcel.client_id, func.min(Parcel.arrived_at)).where(
            Parcel.company_id == company.id, Parcel.status.in_(AT_PVZ), Parcel.client_id.is_not(None))
            .group_by(Parcel.client_id))).all())
    for client, debt, _ in await debtors(company.id):
        first = firsts.get(client.id)
        if first is None:
            continue
        days = (now - as_utc(first)).days
        if days < 2:
            continue
        tag = as_utc(first).strftime("%Y%m%d")
        if days >= 5:
            await once(company.id, client.id, f"pay2:{tag}")      # «2 дня» уже не нужно
            send = await once(company.id, client.id, f"pay5:{tag}")
        else:
            send = await once(company.id, client.id, f"pay2:{tag}")
        if send:
            await notify.send_to_client(bot, client, ct(client.lang, "n_remind_pay", sum=fmt_money(debt)))
    # платное хранение скоро: за 2 дня до конца бесплатного
    if st.free_storage_days is not None and st.storage_price_day:
        async with Session() as s:
            rows = (await s.execute(
                select(Parcel.client_id, func.min(Parcel.arrived_at)).where(
                    Parcel.company_id == company.id, Parcel.status.in_(AT_PVZ), Parcel.client_id.is_not(None),
                    Parcel.arrived_at.is_not(None)).group_by(Parcel.client_id))).all()
            clients = {c.id: c for c in (await s.execute(select(Client).where(
                Client.company_id == company.id, Client.id.in_([r[0] for r in rows] or [0])))).scalars().all()}
        for cid, first in rows:
            left = st.free_storage_days - (now - as_utc(first)).days
            if 0 <= left <= 2 and await once(company.id, cid, f"stor:{as_utc(first):%Y%m%d}"):
                await notify.send_to_client(bot, clients[cid], ct(clients[cid].lang, "n_storage_soon",
                                                                  price=f"{fmt_price(st.storage_price_day)}"))


async def daily_report(manager, company: Company) -> None:
    local = reports.local_now()
    if local.hour < config.daily_report_hour:
        return
    if not await once(company.id, 0, f"daily:{local:%Y%m%d}"):
        return
    bot = manager.get_bot(company.id)
    if bot:
        r = await reports.build_report(company.id, 1)
        await notify.send(bot, company.owner_tg_id, reports.report_text(r, "Отчёт дня"))


async def run_once(manager, now=None, hourly: bool = True) -> None:
    now = now or utcnow()
    await run_scheduled_broadcasts(manager, now)
    if not hourly:
        return
    async with Session() as s:
        rows = (await s.execute(select(Company, CompanySettings).join(
            CompanySettings, CompanySettings.company_id == Company.id).where(
            Company.status.in_((CompanyStatus.trial, CompanyStatus.active, CompanyStatus.grace)),
            CompanySettings.setup_done.is_(True)))).all()
    for company, st in rows:
        try:
            async with Session() as s:
                await update_storage(s, st, now)
                await s.commit()
            await client_reminders(manager, company, st, now)
            await daily_report(manager, company)
        except Exception:
            log.exception("maintenance for company %s", company.id)


async def loop(manager) -> None:
    await resume_broadcasts(manager)
    tick = 0
    while True:
        try:
            await run_once(manager, hourly=(tick % 60 == 0))
        except Exception:
            log.exception("maintenance tick")
        tick += 1
        await asyncio.sleep(60)
