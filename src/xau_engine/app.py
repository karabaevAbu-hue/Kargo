"""Сборка приложения: БД, сервис, бот, планировщик фида, веб-сервер."""
from __future__ import annotations

import asyncio
import logging
import sys

from .bot.delivery import Delivery
from .config import Settings
from .db import Database
from .server import create_app
from .service import Service

log = logging.getLogger("xau_engine.app")


def build_app(cfg: Settings):
    db = Database(cfg.db_path)
    service = Service(db, cfg)
    bot = dp = None
    if not cfg.dry_run:
        if not cfg.bot_token:
            sys.exit("Не задан BOT_TOKEN (или включи DRY_RUN=1 для проверки без Telegram).")
        from aiogram import Bot, Dispatcher

        from .bot.handlers import build_router

        bot = Bot(cfg.bot_token)
        dp = Dispatcher()
        dp.include_router(build_router(db, service, cfg))
    delivery = Delivery(bot, db, service, cfg)

    background = []
    if dp is not None:
        async def run_bot(stop: asyncio.Event) -> None:
            log.info("бот запущен")
            await dp.start_polling(bot, handle_signals=False)

        background.append(run_bot)
    if cfg.mode in ("B", "BOTH"):
        from .feed.runner import FeedRunner
        from .feed.twelvedata import TwelveDataClient

        client = TwelveDataClient(cfg.twelvedata_api_key, cfg.td_symbol)
        runner = FeedRunner(service, client, delivery.send)
        background.append(runner.run)
    app = create_app(cfg, db, service, delivery, background)
    return app, db, service, delivery
