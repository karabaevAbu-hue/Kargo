"""Запуск и остановка ботов карго (ТЗ, раздел 2).

Один процесс держит все боты. Telegram шлёт обновления каждого бота на
https://BASE_URL/bot/<company_id>, сервер по company_id находит бота и
передаёт обновление в общий диспетчер с company_id в контексте.
"""
from __future__ import annotations

import asyncio
import logging
import secrets

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import Update
from aiohttp import web
from sqlalchemy import select

from cargo_platform.config import config
from cargo_platform.db.base import Session
from cargo_platform.db.models import BotStatus, Company, CompanyBot, ErrorLog
from cargo_platform.services.crypto import decrypt

log = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def make_bot(token: str) -> Bot:
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def new_secret() -> str:
    return secrets.token_urlsafe(32)


class BotManager:
    def __init__(self, tenant_dp: Dispatcher):
        self.dp = tenant_dp
        self.master: Bot | None = None
        self._bots: dict[int, tuple[Bot, str]] = {}   # company_id -> (Bot, webhook_secret)
        self._tasks: set[asyncio.Task] = set()

    def webhook_url(self, company_id: int) -> str:
        return f"{config.base_url}/bot/{company_id}"

    def is_running(self, company_id: int) -> bool:
        return company_id in self._bots

    def get_bot(self, company_id: int) -> Bot | None:
        item = self._bots.get(company_id)
        return item[0] if item else None

    async def start(self, company_id: int, token: str, secret: str) -> None:
        """Ставит webhook и регистрирует бота. Бросает TelegramUnauthorizedError при плохом токене."""
        await self.stop(company_id, delete_webhook=False)
        bot = make_bot(token)
        try:
            await bot.set_webhook(
                self.webhook_url(company_id),
                secret_token=secret,
                allowed_updates=self.dp.resolve_used_update_types(),
            )
        except Exception:
            await bot.session.close()
            raise
        self._bots[company_id] = (bot, secret)
        log.info("Бот карго %s запущен", company_id)

    async def stop(self, company_id: int, delete_webhook: bool = True) -> None:
        item = self._bots.pop(company_id, None)
        if not item:
            return
        bot, _ = item
        if delete_webhook:
            try:
                await bot.delete_webhook()
            except Exception as e:  # токен мог быть уже отозван
                log.warning("delete_webhook %s: %s", company_id, e)
        await bot.session.close()

    async def start_all(self) -> None:
        """При старте сервера поднимает все боты со статусом running (ТЗ, раздел 9)."""
        async with Session() as s:
            rows = (await s.execute(
                select(CompanyBot).where(CompanyBot.status == BotStatus.running)
            )).scalars().all()
        for row in rows:
            try:
                await self.start(row.company_id, decrypt(row.token_encrypted), row.webhook_secret)
            except TelegramUnauthorizedError as e:
                await self.mark_error(row.company_id, f"Токен отозван: {e}")
            except Exception as e:
                log.exception("Не удалось запустить бота %s", row.company_id)
                await self.mark_error(row.company_id, str(e), notify=False)

    async def stop_all(self) -> None:
        for cid in list(self._bots):
            await self.stop(cid, delete_webhook=False)

    async def mark_error(self, company_id: int, error: str, notify: bool = True) -> None:
        """Бот перестал работать: статус error + сообщение владельцу в главном боте."""
        async with Session() as s:
            row = await s.scalar(select(CompanyBot).where(CompanyBot.company_id == company_id))
            company = await s.get(Company, company_id)
            if row:
                row.status = BotStatus.error
                row.last_error = error[:1000]
            s.add(ErrorLog(company_id=company_id, text=error[:1000]))
            await s.commit()
        await self.stop(company_id, delete_webhook=False)
        if notify and self.master and company:
            try:
                await self.master.send_message(
                    company.owner_tg_id,
                    "⚠️ Ваш бот перестал работать: токен недействителен.\n"
                    "Откройте «Мой бот» → «Сменить токен» и пришлите новый токен из @BotFather. "
                    "Данные клиентов не потеряны.",
                )
            except Exception:
                log.exception("Не удалось уведомить владельца %s", company_id)

    # ---------- приём обновлений ----------

    async def handle(self, request: web.Request) -> web.Response:
        try:
            company_id = int(request.match_info["company_id"])
        except ValueError:
            return web.Response(status=404)
        item = self._bots.get(company_id)
        if not item:
            return web.Response(status=404)
        bot, secret = item
        if not secrets.compare_digest(request.headers.get(SECRET_HEADER, ""), secret):
            return web.Response(status=401)
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        # Отвечаем Telegram сразу, обработка идёт в фоне
        task = asyncio.create_task(self._process(bot, update, company_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return web.Response(text="ok")

    async def _process(self, bot: Bot, update: Update, company_id: int) -> None:
        # Ошибка в одном боте не роняет остальные (ТЗ, раздел 10)
        try:
            await self.dp.feed_update(bot, update, company_id=company_id)
        except TelegramUnauthorizedError as e:
            await self.mark_error(company_id, f"Токен отозван: {e}")
        except Exception as e:
            log.exception("Ошибка обработки обновления бота %s", company_id)
            try:      # и в журнал ошибок раздела «Платформа»
                async with Session() as s:
                    s.add(ErrorLog(company_id=company_id, text=f"{type(e).__name__}: {e}"[:1000]))
                    await s.commit()
            except Exception:
                log.exception("не удалось записать ошибку в журнал")
