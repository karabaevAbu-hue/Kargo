"""Контекст каждого обновления бота карго: компания, настройки, клиент, менеджер.

company_id приходит из BotManager. Здесь же применяются правила доступа:
- компания заблокирована платформой — все получают заглушку;
- подписка не оплачена (после льготных дней) — клиенты получают заглушку, менеджеры
  могут только выгрузить данные (limited=True, ТЗ раздел 9);
- клиент заблокирован менеджером — ограничение доступа.
"""
from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from cargo_platform.client_bot.texts import ct
from cargo_platform.db.base import Session
from cargo_platform.db.models import Client, ClientStatus, Company, CompanyStatus, Staff


class TenantMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        company_id = data.get("company_id")
        user = data.get("event_from_user")
        if company_id is None or user is None:
            return await handler(event, data)
        async with Session() as s:
            company = await s.scalar(select(Company).where(Company.id == company_id)
                                     .options(selectinload(Company.settings)))
            client = await s.scalar(select(Client).where(Client.company_id == company_id, Client.tg_id == user.id))
            staff = await s.scalar(select(Staff).where(
                Staff.company_id == company_id, Staff.tg_id == user.id, Staff.active))
            if client:
                changed = False
                if client.status == ClientStatus.left:      # написал снова — значит, бот разблокирован
                    client.status, changed = ClientStatus.active, True
                if client.username != user.username:
                    client.username, changed = user.username, True
                if changed:
                    await s.commit()
        lang = client.lang if client else (company.lang if company else "ru")
        data.update(company=company, st=company.settings if company else None, client=client,
                    staff=staff, lang=lang, limited=False)

        if company is None:
            return
        blocked = company.status == CompanyStatus.blocked
        paused = company.status == CompanyStatus.paused
        if blocked or (paused and not staff):
            return await _stub(event, ct(lang, "unavailable"))
        if paused and staff:
            data["limited"] = True
            if isinstance(event, Message) and not (event.text or "").startswith("/admin"):
                return await _stub(event, ct(lang, "unavailable"))
        elif client and client.status == ClientStatus.blocked and not staff:
            return await _stub(event, ct(lang, "client_blocked"))
        return await handler(event, data)


async def _stub(event, text: str):
    if isinstance(event, Message):
        await event.answer(text)
    elif isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
