"""Планировщик варианта B: M5 раз в 5 минут (288 запросов в день), D1 и W раз в сутки."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from ..models import Candle
from ..service import HISTORY_M5, OutMessage, Service
from .twelvedata import FeedError, TwelveDataClient

log = logging.getLogger("xau_engine.feed")
M5 = 300
POLL_DELAY_S = 20  # ждём, пока провайдер соберёт свечу
HTF_REFRESH_S = 20 * 3600

Deliver = Callable[[list[OutMessage]], Awaitable[None]]


def closed_only(candles: list[Candle], period: int, now: int) -> list[Candle]:
    return [c for c in candles if c.t + period <= now]


def market_closed(now: int) -> bool:
    """Выходные: с пятницы 22:00 до воскресенья 22:00 UTC запросы не нужны."""
    t = time.gmtime(now)
    wd, h = t.tm_wday, t.tm_hour  # понедельник = 0
    return wd == 5 or (wd == 4 and h >= 22) or (wd == 6 and h < 22)


class FeedRunner:
    def __init__(self, service: Service, client: TwelveDataClient, deliver: Deliver):
        self.service = service
        self.client = client
        self.deliver = deliver
        self._htf_at = 0.0

    async def backfill(self) -> None:
        """Стартовая загрузка истории: один запрос на ТФ."""
        now = int(time.time())
        m5 = await self.client.series("M5", HISTORY_M5)
        self.service.ingest("M5", closed_only(m5, M5, now))
        await self.refresh_htf(now)
        log.info("история загружена: M5=%s", len(self.service.db.load_candles("M5", HISTORY_M5)))

    async def refresh_htf(self, now: int) -> None:
        d1 = await self.client.series("D1", 400)
        w = await self.client.series("W", 200)
        self.service.ingest("D1", d1)
        self.service.ingest("W", w)
        self._htf_at = time.monotonic()

    async def poll_once(self) -> None:
        now = int(time.time())
        if market_closed(now):
            return
        m5 = await self.client.series("M5", 24)  # запас на случай пропущенных опросов
        self.service.ingest("M5", closed_only(m5, M5, now))
        if time.monotonic() - self._htf_at > HTF_REFRESH_S:
            await self.refresh_htf(now)
        msgs = await asyncio.to_thread(self.service.step)
        if msgs:
            await self.deliver(msgs)

    async def run(self, stop: asyncio.Event) -> None:
        try:
            await self.backfill()
            msgs = await asyncio.to_thread(self.service.step)
            if msgs:
                await self.deliver(msgs)
        except (FeedError, Exception) as exc:  # noqa: BLE001
            log.error("стартовая загрузка не удалась: %s", exc)
        while not stop.is_set():
            now = time.time()
            nxt = (int(now) // M5 + 1) * M5 + POLL_DELAY_S
            try:
                await asyncio.wait_for(stop.wait(), timeout=nxt - now)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await self.poll_once()
            except Exception as exc:  # noqa: BLE001
                log.error("опрос фида: %s", exc)
