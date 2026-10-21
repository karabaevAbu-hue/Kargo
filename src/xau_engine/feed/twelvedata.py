"""Клиент Twelve Data (запасной фид, вариант B). Лимит бесплатного тарифа: 8 запросов в минуту, 800 в день."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import httpx

from ..models import Candle

BASE_URL = "https://api.twelvedata.com/time_series"
INTERVALS = {"M5": "5min", "D1": "1day", "W": "1week"}
MIN_GAP_S = 7.6  # 60 / 8 запросов


class FeedError(RuntimeError):
    pass


def parse_values(values: list[dict], daily: bool) -> list[Candle]:
    fmt = "%Y-%m-%d" if daily else "%Y-%m-%d %H:%M:%S"
    out = []
    for v in values:
        dt = datetime.strptime(v["datetime"], fmt).replace(tzinfo=timezone.utc)
        out.append(Candle(int(dt.timestamp()), float(v["open"]), float(v["high"]), float(v["low"]), float(v["close"])))
    out.sort(key=lambda c: c.t)
    return out


class TwelveDataClient:
    def __init__(self, api_key: str, symbol: str = "XAU/USD", http: httpx.AsyncClient | None = None):
        if not api_key:
            raise FeedError("не задан TWELVEDATA_API_KEY")
        self.api_key = api_key
        self.symbol = symbol
        self._http = http or httpx.AsyncClient(timeout=20)
        self._last_call = 0.0
        self._lock = asyncio.Lock()
        self.requests_today = 0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def series(self, tf: str, outputsize: int, end_date: str | None = None) -> list[Candle]:
        """Свечи ТФ (M5 / D1 / W) по возрастанию времени, время в UTC. Последняя свеча может быть незакрытой."""
        params = {
            "symbol": self.symbol,
            "interval": INTERVALS[tf],
            "outputsize": min(outputsize, 5000),
            "timezone": "UTC",
            "order": "ASC",
            "apikey": self.api_key,
        }
        if end_date:
            params["end_date"] = end_date
        async with self._lock:
            wait = MIN_GAP_S - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await self._http.get(BASE_URL, params=params)
            self._last_call = time.monotonic()
            self.requests_today += 1
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "error" or "values" not in data:
            raise FeedError(f"Twelve Data: {data.get('code')} {data.get('message')}")
        return parse_values(data["values"], daily=tf != "M5")

    async def history(self, tf: str, since_t: int, until_t: int | None = None) -> list[Candle]:
        """Глубокая история страницами по 5000 свечей назад от until_t до since_t."""
        out: dict[int, Candle] = {}
        end = until_t
        daily = tf != "M5"
        for _ in range(40):
            end_s = None
            if end is not None:
                dt = datetime.fromtimestamp(end, timezone.utc)
                end_s = dt.strftime("%Y-%m-%d" if daily else "%Y-%m-%d %H:%M:%S")
            page = await self.series(tf, 5000, end_date=end_s)
            if not page:
                break
            fresh = [c for c in page if c.t not in out]
            for c in page:
                out[c.t] = c
            if page[0].t <= since_t or not fresh:
                break
            end = page[0].t - 1
        return sorted((c for c in out.values() if c.t >= since_t), key=lambda c: c.t)
