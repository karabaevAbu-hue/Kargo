"""FastAPI: приём вебхуков TradingView (вариант A), проверка секрета, запуск бота и планировщика."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from .bot.delivery import Delivery
from .config import Settings
from .db import Database
from .service import Service

log = logging.getLogger("xau_engine.server")


def _secret_ok(expected: str, *candidates: Any) -> bool:
    return any(c and hmac.compare_digest(str(c), expected) for c in candidates)


def create_app(
    cfg: Settings,
    db: Database,
    service: Service,
    delivery: Delivery,
    background: list | None = None,
) -> FastAPI:
    """background — корутины-фабрики (бот, планировщик), стартуют вместе с приложением."""
    tasks: list[asyncio.Task] = []
    stop = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        for factory in background or []:
            tasks.append(asyncio.create_task(factory(stop)))
        yield
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="XAU Engine Pro", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        m5 = await asyncio.to_thread(db.last_candle_t, "M5")
        return {"ok": True, "mode": cfg.mode, "last_m5": m5, "active_signals": len(db.active_signals())}

    @app.post("/webhook/tradingview")
    async def tradingview(request: Request) -> dict:
        if not cfg.webhook_secret:
            raise HTTPException(503, "вебхук выключен: задай WEBHOOK_SECRET")
        raw = (await request.body()).decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            raise HTTPException(400, "ожидается JSON-объект") from None
        # TradingView не умеет заголовки: секрет принимаем в теле, в query или в заголовке
        given = (payload.pop("secret", None), request.query_params.get("secret"),
                 request.headers.get("x-webhook-secret"))
        if not _secret_ok(cfg.webhook_secret, *given):
            raise HTTPException(403, "неверный секрет")
        if cfg.mode == "B" and str(payload.get("type", "")).lower() in ("signal", "watch"):
            return {"ok": True, "ignored": "режим B: сигналы считает сервер"}
        try:
            msgs = await asyncio.to_thread(service.handle_webhook, payload)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, f"некорректный алерт: {exc}") from exc
        if msgs:
            await delivery.send(msgs)
        return {"ok": True, "messages": len(msgs)}

    return app
