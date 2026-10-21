"""Доставка сообщений подписчикам: фильтры пользователя, тихие часы, график-скриншот."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..chart import render_chart
from ..config import Settings
from ..db import Database
from ..engine.candles import resample
from ..service import OutMessage, Service
from ..timeutil import in_quiet_hours, now_ts

log = logging.getLogger("xau_engine.delivery")
CAPTION_LIMIT = 1024


class Delivery:
    """bot=None или dry_run=True: сообщения пишутся в лог и в self.outbox (для проверки без Telegram)."""

    def __init__(self, bot: Any | None, db: Database, service: Service, settings: Settings):
        self.bot = bot
        self.db = db
        self.service = service
        self.cfg = settings
        self.p = settings.params
        self.outbox: list[tuple[int, str, bool]] = []  # (chat_id, text, silent)

    # ------------------------------------------------------------------ фильтры
    def _wants(self, msg: OutMessage, chat_id: int) -> bool:
        s = self.db.user_settings(chat_id)
        sig = msg.signal
        if msg.kind == "SIGNAL":
            if sig.width_pts(self.p) > s["zone_max"] + 1e-9:
                return False
            if s["min_rr"] is not None and sig.tps and sig.rr(0) < s["min_rr"]:
                return False
            return True
        if msg.kind == "WATCH":
            return bool(self.cfg.send_watch and s["send_watch"])
        return chat_id in self.db.delivered_chats(sig.id)  # события — только тем, кто получил сигнал

    def _silent(self, chat_id: int) -> bool:
        s = self.db.user_settings(chat_id)
        return bool(s["quiet"] and in_quiet_hours(now_ts(), self.cfg.quiet_start, self.cfg.quiet_end))

    # ----------------------------------------------------------------- отправка
    async def send(self, msgs: list[OutMessage]) -> None:
        for msg in msgs:
            await self._send_one(msg)

    async def _chart(self, msg: OutMessage) -> bytes | None:
        if not msg.chart:
            return None
        m5 = self.db.load_candles("M5", 2000)
        if len(m5) < 20:
            return None
        m15 = resample(m5, 15)
        price = m5[-1].c
        try:
            return await asyncio.to_thread(render_chart, msg.signal, m15, price, self.p)
        except Exception as exc:  # noqa: BLE001
            log.error("не удалось построить график: %s", exc)
            return None

    async def _send_one(self, msg: OutMessage) -> None:
        chats = [c for c in self.db.subscribers() if self._wants(msg, c)]
        if not chats:
            return
        png = await self._chart(msg)
        for chat_id in chats:
            silent = self._silent(chat_id)
            try:
                await self._deliver(chat_id, msg, png, silent)
            except Exception as exc:  # noqa: BLE001
                name = type(exc).__name__
                if name == "TelegramForbiddenError":
                    self.db.deactivate_user(chat_id)
                    log.warning("пользователь %s заблокировал бота, отписан", chat_id)
                else:
                    log.error("отправка %s не удалась: %s: %s", chat_id, name, exc)
                continue
            if msg.kind == "SIGNAL" and msg.signal.id is not None:
                self.db.add_delivery(msg.signal.id, chat_id)

    async def _deliver(self, chat_id: int, msg: OutMessage, png: bytes | None, silent: bool) -> None:
        self.outbox.append((chat_id, msg.text, silent))
        if self.bot is None or self.cfg.dry_run:
            log.info("[dry-run] -> %s%s\n%s", chat_id, " (без звука)" if silent else "", msg.text)
            return
        from aiogram.exceptions import TelegramRetryAfter
        from aiogram.types import BufferedInputFile

        for attempt in (1, 2):
            try:
                if png is not None and len(msg.text) <= CAPTION_LIMIT * 4:  # <pre> и теги не входят в лимит подписи
                    photo = BufferedInputFile(png, filename="xauusd.png")
                    try:
                        await self.bot.send_photo(chat_id, photo, caption=msg.text, parse_mode="HTML",
                                                  disable_notification=silent)
                        return
                    except Exception as exc:  # подпись длиннее лимита: фото и текст отдельно
                        if "caption" not in str(exc).lower():
                            raise
                        await self.bot.send_photo(chat_id, photo, disable_notification=silent)
                await self.bot.send_message(chat_id, msg.text, parse_mode="HTML", disable_notification=silent)
                return
            except TelegramRetryAfter as exc:
                if attempt == 2:
                    raise
                await asyncio.sleep(exc.retry_after + 1)
