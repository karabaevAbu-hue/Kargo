"""Точка входа: python -m cargo_platform.main

Поднимает веб-сервер с тремя адресами:
  POST /master            — webhook главного бота
  POST /bot/<company_id>  — webhook ботов карго
  GET  /health            — проверка, что сервер жив (для мониторинга)
"""
import asyncio
import logging

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from cargo_platform.client_bot.handlers import router as client_router
from cargo_platform.config import config
from cargo_platform.db.base import init_db
from cargo_platform.master_bot import menu, registration, settings
from cargo_platform.services import maintenance, subscriptions
from cargo_platform.services.bot_manager import BotManager, make_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cargo")


def build_app() -> web.Application:
    if not config.master_token or not config.base_url or not config.fernet_key:
        raise SystemExit("Заполните MASTER_BOT_TOKEN, BASE_URL и FERNET_KEY в .env")

    master_bot = make_bot(config.master_token)
    # На старте хватит памяти; при росте заменить на RedisStorage, чтобы состояния не терялись при перезапуске
    master_dp = Dispatcher(storage=MemoryStorage())
    master_dp.include_routers(registration.router, settings.router, menu.router)

    # MemoryStorage ключует состояния по bot_id: один человек может быть клиентом в нескольких карго без путаницы.
    # При переходе на Redis: RedisStorage(..., key_builder=DefaultKeyBuilder(with_bot_id=True))
    tenant_dp = Dispatcher(storage=MemoryStorage())
    tenant_dp.include_router(client_router)

    manager = BotManager(tenant_dp)
    manager.master = master_bot
    master_dp["manager"] = manager

    bg_tasks: list[asyncio.Task] = []

    async def on_startup(_: Dispatcher):
        await init_db()
        await master_bot.set_webhook(
            f"{config.base_url}/master",
            secret_token=config.master_secret,
            allowed_updates=master_dp.resolve_used_update_types(),
        )
        await manager.start_all()
        bg_tasks.append(asyncio.create_task(subscriptions.loop(master_bot)))
        bg_tasks.append(asyncio.create_task(maintenance.loop(manager)))
        log.info("Сервер запущен: %s", config.base_url)

    async def on_shutdown(_: Dispatcher):
        for task in bg_tasks:
            task.cancel()
        await manager.stop_all()

    master_dp.startup.register(on_startup)
    master_dp.shutdown.register(on_shutdown)

    app = web.Application()
    SimpleRequestHandler(dispatcher=master_dp, bot=master_bot, secret_token=config.master_secret).register(app, path="/master")
    app.router.add_post("/bot/{company_id}", manager.handle)
    app.router.add_get("/health", lambda _: web.Response(text="ok"))
    setup_application(app, master_dp, bot=master_bot)
    return app


def main() -> None:
    web.run_app(build_app(), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
