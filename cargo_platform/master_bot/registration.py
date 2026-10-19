"""Регистрация карго и подключение бота (ТЗ, разделы 3.1–3.2)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from cargo_platform.config import config
from cargo_platform.db.base import Session
from cargo_platform.db.models import (
    BotStatus, Company, CompanyBot, CompanySettings, CompanyStatus, Staff, StaffRole,
)
from cargo_platform.master_bot.common import (
    REMOVE, Connect, Reg, confirm_kb, lang_kb, main_menu, owner_company, phone_kb, skip_kb, slug, utcnow,
)
from cargo_platform.services.bot_manager import BotManager, make_bot, new_secret
from cargo_platform.services.crypto import decrypt, encrypt, sha256
from cargo_platform.services.validators import clean_phone, valid_company_name, valid_name, valid_token
from cargo_platform.texts import all_variants, t

log = logging.getLogger(__name__)
router = Router(name="registration")
_TASKS: set[asyncio.Task] = set()


async def announce_new_bot(old_bot, company_id: int, new_username: str, owner_tg_id: int, master) -> int:
    """Шлёт клиентам из СТАРОГО бота ссылку на новый. Возвращает, сколько получили."""
    from cargo_platform.db.models import Client, ClientStatus
    from cargo_platform.services import notify
    async with Session() as s:
        clients = list((await s.execute(select(Client).where(
            Client.company_id == company_id, Client.status == ClientStatus.active,
            Client.tg_id.is_not(None)))).scalars().all())
    sent = 0
    try:
        for c in clients:
            text = (f"ℹ️ Карго перешло на нового бота: https://t.me/{new_username}\n"
                    "Откройте его — ваш код, посылки и долги сохранены.")
            sent += await notify.send(old_bot, c.tg_id, text) == notify.OK
        try:
            await old_bot.delete_webhook()
        except Exception:
            pass     # токен старого бота мог быть уже отозван
    finally:
        await old_bot.session.close()
    if master is not None:
        await notify.send(master, owner_tg_id, f"📨 Клиентам старого бота отправлена ссылка на нового: {sent} из {len(clients)}.")
    return sent


async def _lang(state: FSMContext) -> str:
    return (await state.get_data()).get("lang", "ru")


# ---------- /start ----------

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    company = await owner_company(message.from_user.id)
    if company is None:
        await state.set_state(Reg.lang)
        await message.answer(t("ru", "choose_lang"), reply_markup=lang_kb())
        return
    lang = company.lang
    if company.bot is None:
        await state.update_data(lang=lang, company_id=company.id)
        await state.set_state(Connect.token)
        await message.answer(t(lang, "token_instructions", company=company.name, company_slug=slug(company.name)))
        return
    if not (company.settings and company.settings.setup_done):
        from cargo_platform.master_bot.settings import start_wizard
        await start_wizard(message, state, company.id, lang)
        return
    await message.answer(t(lang, "menu_title", company=company.name), reply_markup=main_menu(lang, message.from_user.id))


@router.callback_query(Reg.lang, F.data.startswith("lang:"))
async def choose_lang(call: CallbackQuery, state: FSMContext):
    lang = call.data.split(":")[1]
    await state.update_data(lang=lang)
    await call.message.edit_reply_markup()
    await call.message.answer(t(lang, "welcome"))
    await call.message.answer(t(lang, "ask_owner_name"), reply_markup=REMOVE)
    await state.set_state(Reg.owner_name)
    await call.answer()


# ---------- 3.1 регистрация ----------

@router.message(Reg.owner_name, F.text)
async def owner_name(message: Message, state: FSMContext):
    lang = await _lang(state)
    name = valid_name(message.text)
    if not name:
        return await message.answer(t(lang, "bad_name"))
    await state.update_data(owner=name)
    await state.set_state(Reg.phone)
    await message.answer(t(lang, "ask_phone"), reply_markup=phone_kb(lang))


@router.message(Reg.phone, F.contact | F.text)
async def owner_phone(message: Message, state: FSMContext):
    lang = await _lang(state)
    raw = message.contact.phone_number if message.contact else message.text
    phone = clean_phone(raw)
    if not phone:
        return await message.answer(t(lang, "bad_phone"), reply_markup=phone_kb(lang))
    await state.update_data(phone=phone)
    await state.set_state(Reg.company)
    await message.answer(t(lang, "ask_company"), reply_markup=REMOVE)


@router.message(Reg.company, F.text)
async def company_name(message: Message, state: FSMContext):
    lang = await _lang(state)
    name = valid_company_name(message.text)
    if not name:
        return await message.answer(t(lang, "bad_company"))
    async with Session() as s:
        taken = await s.scalar(select(Company.id).where(func.lower(Company.name) == name.lower()))
    if taken:
        return await message.answer(t(lang, "company_taken"))
    await state.update_data(company=name)
    await state.set_state(Reg.city)
    await message.answer(t(lang, "ask_city"))


@router.message(Reg.city, F.text)
async def city(message: Message, state: FSMContext):
    lang = await _lang(state)
    text = message.text.strip()
    if not 2 <= len(text) <= 60:
        return await message.answer(t(lang, "bad_text"))
    await state.update_data(city=text)
    await state.set_state(Reg.pvz)
    await message.answer(t(lang, "ask_pvz"))


@router.message(Reg.pvz, F.text | F.location)
async def pvz(message: Message, state: FSMContext):
    lang = await _lang(state)
    if message.location:
        loc = message.location
        text = f"https://maps.google.com/?q={loc.latitude},{loc.longitude}"
    else:
        text = message.text.strip()
        if not 5 <= len(text) <= 300:
            return await message.answer(t(lang, "bad_text"))
    await state.update_data(pvz=text)
    await state.set_state(Reg.schedule)
    await message.answer(t(lang, "ask_schedule"))


@router.message(Reg.schedule, F.text)
async def schedule(message: Message, state: FSMContext):
    lang = await _lang(state)
    text = message.text.strip()
    if not 3 <= len(text) <= 100:
        return await message.answer(t(lang, "bad_text"))
    await state.update_data(schedule=text)
    await state.set_state(Reg.contact)
    await message.answer(t(lang, "ask_contact"), reply_markup=skip_kb(lang))


@router.message(Reg.contact, F.text)
async def contact(message: Message, state: FSMContext):
    lang = await _lang(state)
    text = None if message.text in all_variants("btn_skip") else message.text.strip()[:100]
    await state.update_data(contact=text)
    d = await state.get_data()
    await state.set_state(Reg.confirm)
    await message.answer("👇", reply_markup=REMOVE)
    await message.answer(
        t(lang, "confirm_company", owner=d["owner"], phone=d["phone"], company=d["company"],
          city=d["city"], pvz=d["pvz"], schedule=d["schedule"], contact=d["contact"] or "—"),
        reply_markup=confirm_kb(lang),
    )


@router.callback_query(Reg.confirm, F.data == "reg:edit")
async def confirm_edit(call: CallbackQuery, state: FSMContext):
    lang = await _lang(state)
    await call.message.edit_reply_markup()
    await state.set_state(Reg.owner_name)
    await call.message.answer(t(lang, "ask_owner_name"))
    await call.answer()


@router.callback_query(Reg.confirm, F.data == "reg:ok")
async def confirm_ok(call: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    lang = d.get("lang", "ru")
    await call.message.edit_reply_markup()
    async with Session() as s:
        # Повторная проверка: за время регистрации имя могли занять
        if await s.scalar(select(Company.id).where(func.lower(Company.name) == d["company"].lower())):
            await state.set_state(Reg.company)
            await call.message.answer(t(lang, "company_taken"))
            return await call.answer()
        company = Company(
            name=d["company"], owner_name=d["owner"], owner_phone=d["phone"],
            owner_tg_id=call.from_user.id, city=d["city"], pvz_address=d["pvz"],
            schedule=d["schedule"], contact=d["contact"], lang=lang,
            status=CompanyStatus.trial, tariff="trial",
            paid_until=utcnow() + timedelta(days=config.trial_days),
        )
        s.add(company)
        await s.flush()
        s.add(CompanySettings(company_id=company.id))
        s.add(Staff(company_id=company.id, tg_id=call.from_user.id, name=d["owner"], role=StaffRole.owner))
        await s.commit()
        company_id, name = company.id, company.name
    await state.set_data({"lang": lang, "company_id": company_id})
    await state.set_state(Connect.token)
    await call.message.answer(t(lang, "company_created", days=config.trial_days))
    await call.message.answer(t(lang, "token_instructions", company=name, company_slug=slug(name)))
    await call.answer()


# ---------- 3.2 подключение бота (и смена токена) ----------

@router.message(StateFilter(Connect.token), F.text)
async def receive_token(message: Message, state: FSMContext, manager: BotManager):
    d = await state.get_data()
    lang = d.get("lang", "ru")
    company_id = d["company_id"]
    token = valid_token(message.text)
    # Токен — секрет: удаляем сообщение из чата
    try:
        await message.delete()
    except Exception:
        pass
    if not token:
        return await message.answer(t(lang, "bad_token"))
    if not config.base_url:
        return await message.answer(t(lang, "no_base_url"))

    token_hash = sha256(token)
    async with Session() as s:
        other = await s.scalar(select(CompanyBot).where(CompanyBot.token_hash == token_hash))
    if other and other.company_id != company_id:
        return await message.answer(t(lang, "token_taken"))

    # Проверка токена через getMe
    probe = make_bot(token)
    try:
        me = await probe.get_me()
    except TelegramUnauthorizedError:
        return await message.answer(t(lang, "token_rejected"))
    except Exception as e:
        return await message.answer(t(lang, "bot_start_failed", error=str(e)[:200]))
    finally:
        await probe.session.close()

    async with Session() as s:
        prev = await s.scalar(select(CompanyBot).where(CompanyBot.company_id == company_id))
        prev_token = decrypt(prev.token_encrypted) if prev else None
        prev_username = prev.bot_username if prev else None

    secret = new_secret()
    try:
        await manager.start(company_id, token, secret)
    except Exception as e:
        log.exception("start bot")
        return await message.answer(t(lang, "bot_start_failed", error=str(e)[:200]))

    async with Session() as s:
        row = await s.scalar(select(CompanyBot).where(CompanyBot.company_id == company_id))
        if row is None:
            row = CompanyBot(company_id=company_id)
            s.add(row)
        row.bot_tg_id = me.id
        row.bot_username = me.username
        row.token_encrypted = encrypt(token)
        row.token_hash = token_hash
        row.webhook_secret = secret
        row.status = BotStatus.running
        row.last_error = None
        await s.commit()

    await message.answer(t(lang, "token_deleted_note"))
    await message.answer(t(lang, "bot_connected", username=me.username))
    if prev_token and prev_username != me.username:
        # Владелец сменил бота: клиентам старого бота уходит ссылка на новый, пока старый ещё работает (ТЗ, раздел 9)
        task = asyncio.create_task(announce_new_bot(make_bot(prev_token), company_id, me.username,
                                                    message.from_user.id, manager.master))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)

    company = await owner_company(message.from_user.id)
    if company and company.settings and company.settings.setup_done:
        await state.clear()
        await message.answer(t(lang, "menu_title", company=company.name),
                             reply_markup=main_menu(lang, message.from_user.id))
    else:
        from cargo_platform.master_bot.settings import start_wizard
        await start_wizard(message, state, company_id, lang)
