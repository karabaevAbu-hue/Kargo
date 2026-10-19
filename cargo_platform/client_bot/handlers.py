"""Бот карго — неделя 1.

Что уже работает: проверка статуса компании, карточка карго для клиента,
приём менеджеров по ссылке-приглашению, вход в /admin.
Регистрация клиентов — неделя 2, склад — неделя 3, деньги и выдача — неделя 4.

company_id приходит в каждый обработчик из BotManager (feed_update(..., company_id=...)).
"""
from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from cargo_platform.db.base import Session
from cargo_platform.db.models import Company, CompanyStatus, Staff, StaffInvite, StaffRole
from cargo_platform.master_bot.common import as_utc, utcnow

router = Router(name="client")

UNAVAILABLE = "⏳ Сервис временно недоступен. Пожалуйста, свяжитесь с карго-компанией напрямую."


async def _company(company_id: int) -> Company | None:
    async with Session() as s:
        return await s.scalar(
            select(Company).where(Company.id == company_id).options(selectinload(Company.settings))
        )


async def _staff(company_id: int, tg_id: int) -> Staff | None:
    async with Session() as s:
        return await s.scalar(select(Staff).where(
            Staff.company_id == company_id, Staff.tg_id == tg_id, Staff.active))


@router.message(CommandStart(deep_link=True))
async def start_deeplink(message: Message, command: CommandObject, company_id: int):
    arg = command.args or ""
    if not arg.startswith("staff_"):
        return await start(message, company_id)
    token = arg.removeprefix("staff_")
    async with Session() as s:
        inv = await s.scalar(select(StaffInvite).where(StaffInvite.token == token))
        if not inv or inv.used or inv.company_id != company_id or as_utc(inv.expires_at) < utcnow():
            return await message.answer("Ссылка недействительна или устарела. Попросите владельца прислать новую.")
        existing = await s.scalar(select(Staff).where(Staff.company_id == company_id,
                                                      Staff.tg_id == message.from_user.id))
        if existing:
            existing.active = True
        else:
            s.add(Staff(company_id=company_id, tg_id=message.from_user.id,
                        name=message.from_user.full_name[:60], role=StaffRole.admin))
        inv.used = True
        await s.commit()
    await message.answer("✅ Вы добавлены как менеджер. Админка: /admin")


@router.message(CommandStart())
async def start(message: Message, company_id: int):
    c = await _company(company_id)
    if not c or c.status in (CompanyStatus.paused, CompanyStatus.blocked):
        return await message.answer(UNAVAILABLE)
    st = c.settings
    if not st or not st.setup_done:
        return await message.answer("🔧 Бот настраивается. Загляните чуть позже.")
    lines = [
        f"👋 Здравствуйте! Это бот карго <b>{c.name}</b>.",
        st.welcome_text or "",
        f"📍 ПВЗ: {c.pvz_address}",
        f"🕒 {c.schedule}",
        f"💰 {st.price_per_kg:g} сом/кг, доставка {st.delivery_days}",
        "\nРегистрация клиентов откроется в ближайшее время.",
    ]
    await message.answer("\n".join(x for x in lines if x), disable_web_page_preview=True)


@router.message(Command("admin"))
async def admin(message: Message, company_id: int):
    staff = await _staff(company_id, message.from_user.id)
    if not staff:
        return  # чужим молчим, не раскрываем существование админки
    role = "владелец" if staff.role == StaffRole.owner else "менеджер"
    await message.answer(
        f"🛠 Админка ({role}).\n\nРазделы партий, клиентов, оплат и выдачи появятся на неделях 3–4 по плану ТЗ."
    )
