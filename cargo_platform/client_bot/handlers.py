"""Бот карго: точка сборки всех разделов.

company_id приходит в каждый обработчик из BotManager (feed_update(..., company_id=...)).
Порядок важен: сначала команды этого файла, затем админка менеджеров (состояния диалогов
менеджера), затем клиентские разделы.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import func, select

from cargo_platform.admin_bot import batches, broadcast, issue, panel, payments, reports
from cargo_platform.client_bot import menu, payment, registration
from cargo_platform.client_bot.common import menu_kb
from cargo_platform.client_bot.middleware import TenantMiddleware
from cargo_platform.client_bot.texts import ct
from cargo_platform.db.base import Session
from cargo_platform.db.models import (
    Client, Company, CompanySettings, Staff, StaffInvite, StaffRole,
)
from cargo_platform.master_bot.common import as_utc, utcnow
from cargo_platform.services.billing import fmt_price
from cargo_platform.services.tariffs import staff_limit

router = Router(name="tenant")
_mw = TenantMiddleware()
router.message.outer_middleware(_mw)
router.callback_query.outer_middleware(_mw)
router.message.filter(F.chat.type == "private")


@router.message(CommandStart(deep_link=True))
async def start_deeplink(message: Message, command: CommandObject, state: FSMContext, company: Company,
                         st: CompanySettings, client: Client | None, staff: Staff | None, lang: str):
    arg = command.args or ""
    if not arg.startswith("staff_"):
        return await start(message, state, company, st, client, staff, lang)
    token = arg.removeprefix("staff_")
    async with Session() as s:
        inv = await s.scalar(select(StaffInvite).where(StaffInvite.token == token))
        if not inv or inv.used or inv.company_id != company.id or as_utc(inv.expires_at) < utcnow():
            return await message.answer("Ссылка недействительна или устарела. Попросите владельца прислать новую.")
        existing = await s.scalar(select(Staff).where(Staff.company_id == company.id,
                                                      Staff.tg_id == message.from_user.id))
        if not (existing and existing.active):
            limit = staff_limit(company.tariff)
            admins = await s.scalar(select(func.count()).select_from(Staff).where(
                Staff.company_id == company.id, Staff.role == StaffRole.admin, Staff.active))
            if limit is not None and admins >= limit:
                return await message.answer("На тарифе карго достигнут лимит менеджеров. Сообщите владельцу.")
        if existing:
            existing.active = True
        else:
            s.add(Staff(company_id=company.id, tg_id=message.from_user.id,
                        name=message.from_user.full_name[:60], role=StaffRole.admin))
        inv.used = True
        await s.commit()
    await message.answer("✅ Вы добавлены как менеджер. Админка: /admin")


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, company: Company, st: CompanySettings,
                client: Client | None, staff: Staff | None, lang: str):
    await state.clear()
    if not st or not st.setup_done:
        return await message.answer(ct(lang, "setup_in_progress"))
    if client:
        return await message.answer(f"{ct(lang, 'menu_title')} · <b>{client.code}</b>", reply_markup=menu_kb(client.lang))
    lines = [
        f"👋 Здравствуйте! Это бот карго <b>{company.name}</b>.",
        st.welcome_text or "",
        f"📍 ПВЗ: {company.pvz_address}",
        f"🕒 {company.schedule}",
        f"💰 {fmt_price(st.price_per_kg)} сом/кг, доставка {st.delivery_days}",
    ]
    if staff:
        lines.append("\n🛠 Вы менеджер этого карго: админка — /admin")
    await message.answer("\n".join(x for x in lines if x), disable_web_page_preview=True)
    await registration.begin(message, state, lang)


@router.message(Command("admin"))
async def admin(message: Message, state: FSMContext, staff: Staff | None, limited: bool):
    if not staff:
        return  # чужим молчим, не раскрываем существование админки
    await state.clear()
    await panel.show_menu(message, staff, limited)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext, client: Client | None, lang: str):
    await state.clear()
    await message.answer(ct(lang, "cancelled"), reply_markup=menu_kb(lang) if client else None)


router.include_routers(
    panel.router, batches.router, issue.router, payments.router, broadcast.router, reports.router,
    registration.router, menu.router, payment.router,
)
