"""Меню владельца (ТЗ, 3.4–3.5) и раздел «Платформа» для Абу (ТЗ, 3.6)."""
from __future__ import annotations

import secrets
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from cargo_platform.config import config
from cargo_platform.db.base import Session
from cargo_platform.db.models import (
    BotStatus, Client, Company, CompanyBot, CompanyStatus, Staff, StaffInvite, StaffRole, Subscription,
)
from cargo_platform.master_bot.common import (
    CompanyEdit, Connect, as_utc, cancel_kb, is_platform_admin, main_menu, owner_company, slug, utcnow,
)
from cargo_platform.master_bot.settings import show_settings
from cargo_platform.services.bot_manager import BotManager
from cargo_platform.services.crypto import decrypt
from cargo_platform.texts import all_variants, t

router = Router(name="menu")

TARIFFS = {  # ТЗ, 3.4
    "start": ("Старт", 500, 3000, 2),
    "business": ("Бизнес", 2000, 5000, 5),
    "pro": ("Про", None, 8000, None),
}
STATUS_RU = {
    CompanyStatus.trial: "пробный период", CompanyStatus.active: "активна",
    CompanyStatus.grace: "льготный период", CompanyStatus.paused: "приостановлена (не оплачено)",
    CompanyStatus.blocked: "заблокирована",
}


def _btn(key: str):
    return F.text.func(lambda x: x in all_variants(key))


async def _company_or_hint(message: Message):
    company = await owner_company(message.from_user.id)
    if company is None:
        await message.answer("Сначала зарегистрируйте компанию: /start")
    return company


# ---------- Компания ----------

COMPANY_FIELDS = {
    "city": ("Город", 60), "pvz_address": ("Адрес ПВЗ", 300),
    "schedule": ("График", 100), "contact": ("Контакт", 100), "owner_name": ("Имя владельца", 60),
}


@router.message(_btn("btn_company"))
async def company_info(message: Message):
    c = await _company_or_hint(message)
    if not c:
        return
    text = (
        f"🏢 <b>{c.name}</b>\n\n👤 Владелец: {c.owner_name}\n📱 {c.owner_phone}\n🏙 {c.city}\n"
        f"📍 ПВЗ: {c.pvz_address}\n🕒 {c.schedule}\n📞 {c.contact or '—'}"
    )
    kb = [[InlineKeyboardButton(text=f"✏️ {title}", callback_data=f"cedit:{key}")]
          for key, (title, _) in COMPANY_FIELDS.items()]
    kb.append([InlineKeyboardButton(text="🌐 Язык / Тил", callback_data="cedit:lang")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), disable_web_page_preview=True)


@router.callback_query(F.data == "cedit:lang")
async def company_lang(call: CallbackQuery):
    c = await owner_company(call.from_user.id)
    if not c:
        return await call.answer()
    new = "ky" if c.lang == "ru" else "ru"
    async with Session() as s:
        (await s.get(Company, c.id)).lang = new
        await s.commit()
    await call.message.answer(t(new, "saved"), reply_markup=main_menu(new, call.from_user.id))
    await call.answer()


@router.callback_query(F.data.startswith("cedit:"))
async def company_edit_start(call: CallbackQuery, state: FSMContext):
    c = await owner_company(call.from_user.id)
    key = call.data.split(":", 1)[1]
    if not c or key not in COMPANY_FIELDS:
        return await call.answer()
    await state.set_state(CompanyEdit.value)
    await state.set_data({"key": key, "lang": c.lang})
    await call.message.answer(f"Новое значение — {COMPANY_FIELDS[key][0]}:", reply_markup=cancel_kb(c.lang))
    await call.answer()


@router.message(CompanyEdit.value, F.text)
async def company_edit_save(message: Message, state: FSMContext):
    d = await state.get_data()
    lang = d["lang"]
    await state.clear()
    if message.text in all_variants("btn_cancel"):
        return await message.answer(t(lang, "cancelled"), reply_markup=main_menu(lang, message.from_user.id))
    key = d["key"]
    title, limit = COMPANY_FIELDS[key]
    value = message.text.strip()
    if not 2 <= len(value) <= limit:
        await state.set_state(CompanyEdit.value)
        await state.set_data(d)
        return await message.answer(t(lang, "bad_text"))
    c = await owner_company(message.from_user.id)
    async with Session() as s:
        setattr(await s.get(Company, c.id), key, value)
        await s.commit()
    await message.answer(t(lang, "saved"), reply_markup=main_menu(lang, message.from_user.id))


# ---------- Мой бот ----------

@router.message(_btn("btn_bot"))
async def my_bot(message: Message, manager: BotManager):
    c = await _company_or_hint(message)
    if not c:
        return
    if not c.bot:
        return await message.answer(t(c.lang, "need_bot_first"))
    running = manager.is_running(c.id)
    status = "🟢 работает" if running else ("🔴 ошибка: " + (c.bot.last_error or "") if c.bot.status == BotStatus.error else "⚪ остановлен")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Перезапустить", callback_data="bot:restart")],
        [InlineKeyboardButton(text="🔑 Сменить токен", callback_data="bot:token")],
    ])
    await message.answer(f"🤖 Бот: @{c.bot.bot_username}\nСтатус: {status}\nСсылка: https://t.me/{c.bot.bot_username}",
                         reply_markup=kb)


@router.callback_query(F.data == "bot:restart")
async def bot_restart(call: CallbackQuery, manager: BotManager):
    c = await owner_company(call.from_user.id)
    if not c or not c.bot:
        return await call.answer()
    try:
        await manager.start(c.id, decrypt(c.bot.token_encrypted), c.bot.webhook_secret)
    except Exception as e:
        await call.message.answer(f"Не удалось запустить: {str(e)[:200]}\nВозможно, токен отозван — смените его.")
        return await call.answer()
    async with Session() as s:
        row = await s.scalar(select(CompanyBot).where(CompanyBot.company_id == c.id))
        row.status, row.last_error = BotStatus.running, None
        await s.commit()
    await call.message.answer("✅ Бот перезапущен.")
    await call.answer()


@router.callback_query(F.data == "bot:token")
async def bot_token(call: CallbackQuery, state: FSMContext):
    c = await owner_company(call.from_user.id)
    if not c:
        return await call.answer()
    await state.set_data({"lang": c.lang, "company_id": c.id})
    await state.set_state(Connect.token)
    await call.message.answer(
        "Пришлите новый токен. Клиенты, коды и посылки останутся прежними.\n\n"
        + t(c.lang, "token_instructions", company=c.name, company_slug=slug(c.name))
    )
    await call.answer()


# ---------- Настройки ----------

@router.message(_btn("btn_settings"))
async def settings(message: Message):
    c = await _company_or_hint(message)
    if c:
        await show_settings(message, c.id)


# ---------- Менеджеры ----------

@router.message(_btn("btn_staff"))
async def staff_list(message: Message):
    c = await _company_or_hint(message)
    if not c:
        return
    async with Session() as s:
        staff = (await s.execute(select(Staff).where(Staff.company_id == c.id, Staff.active))).scalars().all()
    lines = ["👥 <b>Сотрудники</b>\n"]
    for p in staff:
        role = "владелец" if p.role == StaffRole.owner else "менеджер"
        lines.append(f"• {p.name} — {role}")
    kb = [[InlineKeyboardButton(text="➕ Пригласить менеджера", callback_data="staff:invite")]]
    kb += [[InlineKeyboardButton(text=f"🗑 Убрать {p.name}", callback_data=f"staff:off:{p.id}")]
           for p in staff if p.role == StaffRole.admin]
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data == "staff:invite")
async def staff_invite(call: CallbackQuery):
    c = await owner_company(call.from_user.id)
    if not c or not c.bot:
        await call.message.answer("Сначала подключите бота.")
        return await call.answer()
    token = secrets.token_hex(8)
    async with Session() as s:
        s.add(StaffInvite(company_id=c.id, token=token, expires_at=utcnow() + timedelta(hours=24)))
        await s.commit()
    link = f"https://t.me/{c.bot.bot_username}?start=staff_{token}"
    await call.message.answer(
        f"Отправьте эту ссылку менеджеру. Она одноразовая и действует 24 часа:\n\n{link}\n\n"
        "Менеджер откроет вашего бота и получит доступ к админке (/admin).",
        disable_web_page_preview=True,
    )
    await call.answer()


@router.callback_query(F.data.startswith("staff:off:"))
async def staff_off(call: CallbackQuery):
    c = await owner_company(call.from_user.id)
    staff_id = int(call.data.rsplit(":", 1)[1])
    async with Session() as s:
        p = await s.get(Staff, staff_id)
        if c and p and p.company_id == c.id and p.role == StaffRole.admin:
            p.active = False
            await s.commit()
            await call.message.answer(f"Менеджер {p.name} больше не имеет доступа.")
    await call.answer()


# ---------- Подписка ----------

@router.message(_btn("btn_subscription"))
async def subscription(message: Message):
    c = await _company_or_hint(message)
    if not c:
        return
    async with Session() as s:
        clients = await s.scalar(select(func.count()).select_from(Client).where(Client.company_id == c.id))
    tariffs = "\n".join(
        f"• {name}: {price:,} сом/мес, клиентов {'без лимита' if lim is None else f'до {lim:,}'}".replace(",", " ")
        for name, lim, price, _ in TARIFFS.values()
    )
    support = f"@{config.support_username}" if config.support_username else "в поддержку"
    await message.answer(
        f"💳 <b>Подписка</b>\n\nСтатус: {STATUS_RU[c.status]}\nТариф: {c.tariff}\n"
        f"Оплачено до: {as_utc(c.paid_until):%d.%m.%Y}\nКлиентов: {clients}\n\n<b>Тарифы</b>\n{tariffs}\n\n"
        f"Для оплаты напишите {support}: после оплаты подписка продлевается вручную."
    )


@router.message(_btn("btn_support"))
async def support(message: Message):
    if config.support_username:
        await message.answer(f"🆘 Напишите в поддержку: @{config.support_username}")
    else:
        await message.answer("🆘 Поддержка пока не настроена.")


# ---------- Платформа (только Абу) ----------

@router.message(_btn("btn_platform"))
async def platform(message: Message, manager: BotManager):
    if not is_platform_admin(message.from_user.id):
        return
    async with Session() as s:
        companies = (await s.execute(select(Company).options(selectinload(Company.bot)).order_by(Company.id))).scalars().all()
        counts = dict((await s.execute(select(Client.company_id, func.count()).group_by(Client.company_id))).all())
    lines = [f"🛠 <b>Платформа</b>: {len(companies)} карго, {sum(counts.values())} клиентов\n"]
    for c in companies[:50]:
        bot = "🟢" if manager.is_running(c.id) else ("🔴" if c.bot else "⚪")
        lines.append(f"{bot} #{c.id} <b>{c.name}</b> — {STATUS_RU[c.status]}, {c.tariff}, до {as_utc(c.paid_until):%d.%m}, клиентов {counts.get(c.id, 0)}")
    lines.append(
        "\nКоманды:\n<code>/extend ID ДНЕЙ ТАРИФ СУММА</code> — продлить (тариф: start, business, pro)\n"
        "<code>/block ID</code>, <code>/unblock ID</code>"
    )
    await message.answer("\n".join(lines))


@router.message(Command("extend"))
async def extend(message: Message, command: CommandObject):
    if not is_platform_admin(message.from_user.id):
        return
    try:
        cid, days, tariff, amount = (command.args or "").split()
        cid, days, amount = int(cid), int(days), int(amount)
        assert tariff in TARIFFS and 1 <= days <= 400
    except Exception:
        return await message.answer("Формат: /extend ID ДНЕЙ ТАРИФ СУММА\nНапример: /extend 3 30 start 3000")
    async with Session() as s:
        c = await s.get(Company, cid)
        if not c:
            return await message.answer("Компания не найдена.")
        start = max(as_utc(c.paid_until), utcnow())
        c.paid_until = start + timedelta(days=days)
        c.tariff, c.status, c.notice_key = tariff, CompanyStatus.active, None
        s.add(Subscription(company_id=cid, tariff=tariff, amount=amount, period_from=start,
                           period_to=c.paid_until, confirmed_by=message.from_user.id))
        await s.commit()
        owner, until = c.owner_tg_id, c.paid_until
    await message.answer(f"✅ #{cid} продлена до {until:%d.%m.%Y}, тариф {tariff}.")
    try:
        await message.bot.send_message(owner, f"✅ Подписка продлена до {until:%d.%m.%Y}. Спасибо!")
    except Exception:
        pass


@router.message(Command("block", "unblock"))
async def block(message: Message, command: CommandObject):
    if not is_platform_admin(message.from_user.id):
        return
    try:
        cid = int(command.args)
    except (TypeError, ValueError):
        return await message.answer("Формат: /block ID или /unblock ID")
    blocking = command.command == "block"
    async with Session() as s:
        c = await s.get(Company, cid)
        if not c:
            return await message.answer("Компания не найдена.")
        if blocking:
            c.status = CompanyStatus.blocked
        else:
            c.status = CompanyStatus.active if as_utc(c.paid_until) > utcnow() else CompanyStatus.paused
        await s.commit()
    await message.answer(f"#{cid}: {STATUS_RU[c.status]}")
