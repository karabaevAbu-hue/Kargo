"""Команды и кнопки бота: /start, активные сигналы, история, настройки, анализ сейчас, TradingView."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from ..config import Settings
from ..db import Database
from ..service import Service
from ..texts import active_text, analysis_text, history_text, settings_text, welcome_text
from ..timeutil import day_key, now_ts, week_key
from . import keyboards as kb

log = logging.getLogger("xau_engine.bot")


def build_router(db: Database, service: Service, cfg: Settings) -> Router:
    r = Router()
    p = cfg.params

    def allowed(user_id: int | None) -> bool:
        return not cfg.allowed_user_ids or (user_id in cfg.allowed_user_ids)

    @r.message(CommandStart())
    async def start(m: Message) -> None:
        if not allowed(m.from_user.id if m.from_user else None):
            await m.answer("Нет доступа. Бот закрытый.")
            return
        db.add_user(m.chat.id, m.from_user.username if m.from_user else None, now_ts())
        await m.answer(welcome_text(cfg.max_signals_day, service.feed_label()), parse_mode="HTML",
                       reply_markup=kb.main_keyboard())

    @r.message(Command("stop"))
    async def stop(m: Message) -> None:
        db.deactivate_user(m.chat.id)
        await m.answer("Подписка отключена. Вернуться — /start.")

    @r.message(Command("help"))
    async def help_(m: Message) -> None:
        await m.answer("Кнопки внизу: активные сигналы, история, настройки, анализ сейчас, ссылка на график.\n"
                       "/start — подписка, /stop — отписка.")

    def guard(m: Message) -> bool:
        return allowed(m.from_user.id if m.from_user else None) and m.chat.id in db.subscribers()

    @r.message(F.text == kb.BTN_ACTIVE)
    async def active(m: Message) -> None:
        if not guard(m):
            return await m.answer("Сначала /start.")
        price = service.last_price()
        text = active_text(db.active_signals(), price, p, db.watch_signals(now_ts() - 48 * 3600))
        await m.answer(text, parse_mode="HTML")

    @r.message(F.text == kb.BTN_HISTORY)
    async def history(m: Message) -> None:
        if not guard(m):
            return await m.answer("Сначала /start.")
        now = now_ts()
        text = history_text(db.history(20), db.totals("day", day_key(now)), db.totals("week", week_key(now)), p)
        await m.answer(text, parse_mode="HTML")

    @r.message(F.text == kb.BTN_SETTINGS)
    async def settings(m: Message) -> None:
        if not guard(m):
            return await m.answer("Сначала /start.")
        s = db.user_settings(m.chat.id)
        await m.answer(settings_text(s, cfg.quiet_start, cfg.quiet_end, cfg.max_signals_day), parse_mode="HTML",
                       reply_markup=kb.settings_keyboard(s))

    @r.callback_query(F.data.startswith("set:"))
    async def set_cb(cb: CallbackQuery) -> None:
        chat_id = cb.message.chat.id
        s = db.user_settings(chat_id)
        what = (cb.data or "").split(":", 1)[1]
        if what == "width":
            s = db.update_user_settings(chat_id, zone_max=5 if s["zone_max"] >= 10 else 10)
        elif what == "rr":
            s = db.update_user_settings(chat_id, min_rr=kb.next_rr(s["min_rr"]))
        elif what == "watch":
            s = db.update_user_settings(chat_id, send_watch=not s["send_watch"])
        elif what == "quiet":
            s = db.update_user_settings(chat_id, quiet=not s["quiet"])
        await cb.message.edit_text(settings_text(s, cfg.quiet_start, cfg.quiet_end, cfg.max_signals_day),
                                   parse_mode="HTML", reply_markup=kb.settings_keyboard(s))
        await cb.answer("Сохранено")

    @r.message(F.text == kb.BTN_ANALYSIS)
    async def analysis(m: Message) -> None:
        if not guard(m):
            return await m.answer("Сначала /start.")
        now = now_ts()
        wait = cfg.analysis_cooldown_s - (now - db.last_analysis(m.chat.id))
        if wait > 0:  # «Не чаще раза в 5 минут»
            return await m.answer(f"Разбор можно запрашивать раз в {cfg.analysis_cooldown_s // 60} мин. "
                                  f"Подожди ещё {wait // 60 + 1} мин.")
        db.set_last_analysis(m.chat.id, now)
        a = await asyncio.to_thread(service.current_analysis)
        if not a.last_t and cfg.mode == "A":
            return await m.answer("В режиме A анализ делает индикатор в TradingView, у сервера нет свечей для разового разбора. "
                                  "Разбор по кнопке работает в режимах B и BOTH (нужен ключ Twelve Data).")
        await m.answer(analysis_text(a, p, service.feed_label(), now), parse_mode="HTML")

    @r.message(F.text == kb.BTN_TV)
    async def tv(m: Message) -> None:
        await m.answer("График XAUUSD в TradingView:", reply_markup=kb.tv_keyboard(cfg.tradingview_url))

    return r
