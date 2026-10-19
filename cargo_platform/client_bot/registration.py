"""Регистрация клиента (ТЗ, 4.1–4.2): язык → имя → телефон → поля карго → согласие → код и адрес."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from cargo_platform.client_bot.common import (
    REMOVE, RegFSM, lang_kb, menu_kb, phone_kb, send_address,
)
from cargo_platform.client_bot.texts import ct
from cargo_platform.db.base import Session
from cargo_platform.db.models import Client, Company, CompanySettings, NoticeLog
from cargo_platform.services import notify
from cargo_platform.services.codes import issue_code
from cargo_platform.services.tariffs import client_limit
from cargo_platform.services.validators import clean_phone

router = Router(name="client_registration")
router.message.filter(F.chat.type == "private")


async def begin(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    await state.set_state(RegFSM.lang)
    await message.answer(ct(lang, "choose_lang"), reply_markup=lang_kb())


@router.callback_query(RegFSM.lang, F.data.startswith("clang:"))
async def choose_lang(call: CallbackQuery, state: FSMContext, company: Company, st: CompanySettings):
    lang = call.data.split(":")[1]
    await state.update_data(lang=lang)
    await state.set_state(RegFSM.name)
    await call.message.answer(ct(lang, "welcome", company=company.name, welcome=st.welcome_text or ""))
    await call.message.answer(ct(lang, "ask_name"), reply_markup=REMOVE)
    await call.answer()


async def _lang(state: FSMContext) -> str:
    return (await state.get_data()).get("lang", "ru")


@router.message(RegFSM.name, F.text)
async def get_name(message: Message, state: FSMContext):
    lang = await _lang(state)
    name = " ".join(message.text.split())
    if not 2 <= len(name) <= 60 or name.startswith("/"):
        return await message.answer(ct(lang, "bad_name"))
    await state.update_data(name=name)
    await state.set_state(RegFSM.phone)
    await message.answer(ct(lang, "ask_phone"), reply_markup=phone_kb(lang))


@router.message(RegFSM.phone, F.contact)
async def get_phone_contact(message: Message, state: FSMContext, company: Company, st: CompanySettings, bot: Bot):
    lang = await _lang(state)
    if message.contact.user_id != message.from_user.id:
        return await message.answer(ct(lang, "ask_phone"), reply_markup=phone_kb(lang))
    phone = clean_phone(message.contact.phone_number)
    if not phone:
        return await message.answer(ct(lang, "bad_phone"), reply_markup=phone_kb(lang))
    await _phone_ready(message, state, company, st, phone, verified=True, bot=bot)


@router.message(RegFSM.phone, F.text)
async def get_phone_text(message: Message, state: FSMContext, company: Company, st: CompanySettings, bot: Bot):
    lang = await _lang(state)
    phone = clean_phone(message.text)
    if not phone:
        return await message.answer(ct(lang, "bad_phone"), reply_markup=phone_kb(lang))
    await _phone_ready(message, state, company, st, phone, verified=False, bot=bot)


async def _phone_ready(message: Message, state: FSMContext, company: Company, st: CompanySettings,
                       phone: str, verified: bool, bot: Bot) -> None:
    lang = await _lang(state)
    async with Session() as s:
        existing = await s.scalar(select(Client).where(Client.company_id == company.id, Client.phone == phone))
        if existing:
            mine = existing.tg_id == message.from_user.id
            if mine or (verified and existing.tg_id is None):
                if existing.tg_id is None:       # менеджер отвязал старый Telegram — привязываем новый
                    existing.tg_id, existing.username = message.from_user.id, message.from_user.username
                    existing.lang = lang
                    await s.commit()
                await state.clear()
                await message.answer(ct(existing.lang, "already_registered", code=existing.code),
                                     reply_markup=menu_kb(existing.lang))
                return
            await state.clear()
            key = "phone_taken_other_tg" if verified else "phone_taken"
            return await message.answer(ct(lang, key), reply_markup=REMOVE)
    await state.update_data(phone=phone)
    await _next_step(message, state, st, after="phone")


async def _next_step(message: Message, state: FSMContext, st: CompanySettings, after: str) -> None:
    lang = await _lang(state)
    fields = st.client_fields or {}
    order = ["phone", "city", "address", "consent"]
    for step in order[order.index(after) + 1:]:
        if step == "city" and fields.get("city"):
            await state.set_state(RegFSM.city)
            return await message.answer(ct(lang, "ask_city"), reply_markup=REMOVE)
        if step == "address" and fields.get("delivery_address"):
            await state.set_state(RegFSM.address)
            return await message.answer(ct(lang, "ask_delivery_address"), reply_markup=REMOVE)
        if step == "consent":
            await state.set_state(RegFSM.consent)
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=ct(lang, "btn_agree"), callback_data="reg:agree")]])
            return await message.answer(
                ct(lang, "ask_consent", forbidden=st.forbidden_text or "—"), reply_markup=kb)


@router.message(RegFSM.city, F.text)
async def get_city(message: Message, state: FSMContext, st: CompanySettings):
    lang = await _lang(state)
    text = " ".join(message.text.split())
    if not 2 <= len(text) <= 60:
        return await message.answer(ct(lang, "bad_text"))
    await state.update_data(city=text)
    await _next_step(message, state, st, after="city")


@router.message(RegFSM.address, F.text)
async def get_address(message: Message, state: FSMContext, st: CompanySettings):
    lang = await _lang(state)
    text = " ".join(message.text.split())
    if not 3 <= len(text) <= 200:
        return await message.answer(ct(lang, "bad_text"))
    await state.update_data(delivery_address=text)
    await _next_step(message, state, st, after="address")


@router.callback_query(RegFSM.consent, F.data == "reg:agree")
async def agree(call: CallbackQuery, state: FSMContext, company: Company, st: CompanySettings, bot: Bot):
    d = await state.get_data()
    lang = d.get("lang", "ru")
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)

    limit = client_limit(company.tariff)
    async with Session() as s:
        count = await s.scalar(select(func.count()).select_from(Client).where(Client.company_id == company.id))
    if limit is not None and count >= limit:
        await state.clear()
        await call.message.answer(ct(lang, "limit_reached"))
        await _tell_owner_limit(bot, company, limit)
        return

    extra = {k: d[k] for k in ("delivery_address",) if k in d}
    try:
        async with Session() as s:
            code = await issue_code(s, company.id)
            client = Client(company_id=company.id, code=code, full_name=d["name"], phone=d["phone"],
                            tg_id=call.from_user.id, username=call.from_user.username,
                            city=d.get("city"), extra=extra, lang=lang)
            s.add(client)
            await s.commit()
    except IntegrityError:
        # двойное нажатие или параллельная регистрация: показываем то, что уже есть
        await state.clear()
        async with Session() as s:
            client = await s.scalar(select(Client).where(Client.company_id == company.id,
                                                         Client.tg_id == call.from_user.id))
        if client:
            await call.message.answer(ct(lang, "already_registered", code=client.code), reply_markup=menu_kb(lang))
        return
    await state.clear()
    await call.message.answer(ct(lang, "registered", code=client.code), reply_markup=menu_kb(lang))
    await send_address(bot, call.from_user.id, st, client)


async def _tell_owner_limit(bot: Bot, company: Company, limit: int) -> None:
    """Владельцу — раз в сутки: упёрлись в лимит тарифа."""
    from datetime import date
    key = f"limit:{date.today():%Y%m%d}"
    async with Session() as s:
        if await s.scalar(select(NoticeLog.id).where(NoticeLog.company_id == company.id, NoticeLog.key == key)):
            return
        s.add(NoticeLog(company_id=company.id, client_id=0, key=key))
        await s.commit()
    await notify.send(bot, company.owner_tg_id,
                      f"⚠️ Достигнут лимит клиентов на тарифе ({limit}). Новые клиенты не могут зарегистрироваться. "
                      "Продлите подписку на более высокий тариф в главном боте платформы.")


@router.message(StateFilter(RegFSM.phone), ~F.text & ~F.contact)
async def wrong_content(message: Message, state: FSMContext):
    await message.answer(ct(await _lang(state), "ask_phone"), reply_markup=phone_kb(await _lang(state)))
