"""Общее для главного бота: состояния, клавиатуры, помощники."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from cargo_platform.config import config
from cargo_platform.db.base import Session
from cargo_platform.db.models import Company
from cargo_platform.texts import t


class Reg(StatesGroup):
    lang = State()
    owner_name = State()
    phone = State()
    company = State()
    city = State()
    pvz = State()
    schedule = State()
    contact = State()
    confirm = State()


class Connect(StatesGroup):
    token = State()


class SettingsFSM(StatesGroup):
    value = State()


class CompanyEdit(StatesGroup):
    value = State()


REMOVE = ReplyKeyboardRemove()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """SQLite возвращает даты без часового пояса — считаем их UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s or "my"


def lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"),
        InlineKeyboardButton(text="🇰🇬 Кыргызча", callback_data="lang:ky"),
    ]])


def phone_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "btn_share_phone"), request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


def skip_kb(lang: str, cancel: bool = False) -> ReplyKeyboardMarkup:
    row = [KeyboardButton(text=t(lang, "btn_skip"))]
    if cancel:
        row.append(KeyboardButton(text=t(lang, "btn_cancel")))
    return ReplyKeyboardMarkup(keyboard=[row], resize_keyboard=True)


def cancel_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t(lang, "btn_cancel"))]], resize_keyboard=True)


def confirm_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(lang, "btn_ok"), callback_data="reg:ok"),
        InlineKeyboardButton(text=t(lang, "btn_edit"), callback_data="reg:edit"),
    ]])


def is_platform_admin(tg_id: int) -> bool:
    return tg_id in config.platform_admins


def main_menu(lang: str, tg_id: int) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=t(lang, "btn_company")), KeyboardButton(text=t(lang, "btn_bot"))],
        [KeyboardButton(text=t(lang, "btn_settings")), KeyboardButton(text=t(lang, "btn_staff"))],
        [KeyboardButton(text=t(lang, "btn_subscription")), KeyboardButton(text=t(lang, "btn_support"))],
    ]
    if is_platform_admin(tg_id):
        rows.append([KeyboardButton(text=t(lang, "btn_platform"))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def owner_company(tg_id: int) -> Company | None:
    """Компания, которой владеет этот пользователь (в MVP — одна на владельца)."""
    async with Session() as s:
        return await s.scalar(
            select(Company)
            .where(Company.owner_tg_id == tg_id)
            .options(selectinload(Company.bot), selectinload(Company.settings))
        )
