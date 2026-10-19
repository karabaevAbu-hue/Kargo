"""Отправка сообщений с лимитом 25 в секунду на бота (ТЗ, раздел 7).

Через throttle идёт всё, что бот шлёт клиентам и менеджерам: так Telegram не блокирует отправку.
"""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import update

from cargo_platform.db.base import Session
from cargo_platform.db.models import Client, ClientStatus

log = logging.getLogger(__name__)

RATE_PER_SEC = 25
_MIN_GAP = 1.0 / RATE_PER_SEC
_state: dict[int, tuple[asyncio.Lock, list[float]]] = {}

OK, BLOCKED, ERROR = "ok", "blocked", "error"


async def throttle(bot: Bot) -> None:
    lock, last = _state.setdefault(bot.id, (asyncio.Lock(), [0.0]))
    async with lock:
        wait = last[0] + _MIN_GAP - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        last[0] = time.monotonic()


async def send(bot: Bot, chat_id: int, text: str | None = None, *, photo=None, video=None,
               document=None, reply_markup: InlineKeyboardMarkup | None = None) -> str:
    """Отправляет одно сообщение. Возвращает ok / blocked (бот заблокирован) / error."""
    for _ in range(2):
        await throttle(bot)
        try:
            media = photo or video or document
            if media is not None:
                caption = text if text and len(text) <= 1024 else None
                method = bot.send_photo if photo else bot.send_video if video else bot.send_document
                kwargs = {"photo": photo} if photo else {"video": video} if video else {"document": document}
                await method(chat_id, caption=caption,
                             reply_markup=reply_markup if (caption or not text) else None, **kwargs)
                if text and caption is None:      # длинный текст не влезает в подпись
                    await throttle(bot)
                    await bot.send_message(chat_id, text, reply_markup=reply_markup)
            else:
                await bot.send_message(chat_id, text or "", reply_markup=reply_markup)
            return OK
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
        except TelegramForbiddenError:
            return BLOCKED
        except TelegramBadRequest as e:
            log.warning("send to %s: %s", chat_id, e)
            return ERROR
        except Exception:
            log.exception("send to %s", chat_id)
            return ERROR
    return ERROR


async def send_to_client(bot: Bot, client: Client, text: str | None = None, **kw) -> bool:
    """Шлёт клиенту. Если клиент заблокировал бота — помечает «неактивен», данные и долг сохраняются."""
    if client.tg_id is None or client.status != ClientStatus.active:
        return False
    result = await send(bot, client.tg_id, text, **kw)
    if result == BLOCKED:
        async with Session() as s:
            await s.execute(update(Client).where(Client.id == client.id, Client.company_id == client.company_id)
                            .values(status=ClientStatus.left))
            await s.commit()
    return result == OK
