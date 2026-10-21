"""Клавиатуры бота (раздел 5 ТЗ: команды и кнопки)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

BTN_ACTIVE = "📊 Активные сигналы"
BTN_HISTORY = "📜 История"
BTN_SETTINGS = "⚙️ Настройки"
BTN_ANALYSIS = "🔍 Анализ сейчас"
BTN_TV = "📈 Открыть в TradingView"

RR_STEPS = [None, 1.5, 2.0, 3.0]


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ACTIVE), KeyboardButton(text=BTN_HISTORY)],
            [KeyboardButton(text=BTN_ANALYSIS), KeyboardButton(text=BTN_SETTINGS)],
            [KeyboardButton(text=BTN_TV)],
        ],
        resize_keyboard=True,
    )


def tv_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📈 OANDA:XAUUSD", url=url)]])


def settings_keyboard(s: dict) -> InlineKeyboardMarkup:
    rr = "выкл" if s["min_rr"] is None else f"1:{s['min_rr']:g}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Ширина зоны: до {s['zone_max']} п.", callback_data="set:width")],
            [InlineKeyboardButton(text=f"Минимальный RR: {rr}", callback_data="set:rr")],
            [InlineKeyboardButton(text=f"«На наблюдении»: {'да' if s['send_watch'] else 'нет'}", callback_data="set:watch")],
            [InlineKeyboardButton(text=f"Тихие часы: {'вкл' if s['quiet'] else 'выкл'}", callback_data="set:quiet")],
        ]
    )


def next_rr(cur: float | None) -> float | None:
    i = RR_STEPS.index(cur) if cur in RR_STEPS else 2
    return RR_STEPS[(i + 1) % len(RR_STEPS)]
