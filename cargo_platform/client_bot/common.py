"""Общее для бота карго: состояния, клавиатуры, помощники."""
from __future__ import annotations

from aiogram import Bot
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove,
)

from cargo_platform.client_bot.texts import ct, variants
from cargo_platform.db.models import Client, CompanySettings
from cargo_platform.services import notify
from cargo_platform.services.addresses import build_address


class RegFSM(StatesGroup):
    lang = State()
    name = State()
    phone = State()
    city = State()
    address = State()
    consent = State()


class AddTrack(StatesGroup):
    text = State()


class Calc(StatesGroup):
    weight = State()


class Contact(StatesGroup):
    text = State()


class PayFSM(StatesGroup):
    receipt = State()


MENU_KEYS = ["m_code", "m_add", "m_parcels", "m_pay", "m_calc", "m_tariffs", "m_forbidden",
             "m_pvz", "m_contact", "m_profile"]
REMOVE = ReplyKeyboardRemove()


def all_menu_texts() -> set[str]:
    out: set[str] = set()
    for k in MENU_KEYS:
        out |= variants(k)
    return out


def menu_kb(lang: str) -> ReplyKeyboardMarkup:
    keys = MENU_KEYS
    rows = [[KeyboardButton(text=ct(lang, k)) for k in keys[i:i + 2]] for i in range(0, len(keys), 2)]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="clang:ru"),
        InlineKeyboardButton(text="🇰🇬 Кыргызча", callback_data="clang:ky"),
    ]])


def phone_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=ct(lang, "btn_share_phone"), request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True)


def cancel_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=ct(lang, "cancel"))]], resize_keyboard=True)


def qr_button(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=ct(lang, "qr_for_issue"), callback_data="qr:me")]])


async def send_address(bot: Bot, chat_id: int, st: CompanySettings, client: Client) -> None:
    """Адрес отдельными сообщениями, каждое копируется одним нажатием (ТЗ, 4.2)."""
    lang = client.lang
    addr = build_address(st, client)
    await notify.send(bot, chat_id, f"{ct(lang, 'addr_receiver')}\n<code>{addr['receiver']}</code>")
    if addr["phone"]:
        await notify.send(bot, chat_id, f"{ct(lang, 'addr_phone')}\n<code>{addr['phone']}</code>")
    await notify.send(bot, chat_id, f"{ct(lang, 'addr_address')}\n<code>{addr['address']}</code>")
    if "address_pdd" in addr:
        await notify.send(bot, chat_id, f"{ct(lang, 'addr_pdd')}\n<code>{addr['address_pdd']}</code>")
    await notify.send(bot, chat_id, ct(lang, "addr_howto"))
    await notify.send(bot, chat_id, ct(lang, "addr_warning"), reply_markup=qr_button(lang))


def payment_qr_file(st: CompanySettings):
    from pathlib import Path
    if st.payment_qr_path and Path(st.payment_qr_path).is_file():
        return FSInputFile(st.payment_qr_path)
    return None

