"""Работа с рядами свечей: пересборка таймфреймов, ATR, свинги, имбалансы (FVG)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..models import Candle, Side
from .params import Params

M5 = 300
TF_MINUTES = {"M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
TF_RANK = {"M5": 1, "M15": 2, "M30": 3, "H1": 4, "H4": 5, "D1": 6, "W": 7}


def resample(
    m5: Sequence[Candle],
    minutes: int,
    anchor_hour: int = 0,
    until: int | None = None,
) -> list[Candle]:
    """Собирает свечи нужного ТФ из M5. Берёт только закрытые корзины (конец <= until).

    until по умолчанию — время закрытия последней M5 свечи.
    """
    if not m5:
        return []
    period = minutes * 60
    offset = (anchor_hour * 3600) % period
    if until is None:
        until = m5[-1].t + M5
    out: list[Candle] = []
    cur_start: int | None = None
    o = h = l = c = 0.0
    for k in m5:
        start = ((k.t - offset) // period) * period + offset
        if start != cur_start:
            if cur_start is not None and cur_start + period <= until:
                out.append(Candle(cur_start, o, h, l, c))
            cur_start, o, h, l, c = start, k.o, k.h, k.l, k.c
        else:
            h = max(h, k.h)
            l = min(l, k.l)
            c = k.c
    if cur_start is not None and cur_start + period <= until:
        out.append(Candle(cur_start, o, h, l, c))
    return out


def atr(candles: Sequence[Candle], n: int = 14) -> float:
    """Простой ATR по последним n свечам (среднее истинного диапазона)."""
    if len(candles) < 2:
        return candles[-1].range if candles else 0.0
    start = max(1, len(candles) - n)
    trs = []
    for i in range(start, len(candles)):
        k, p = candles[i], candles[i - 1]
        trs.append(max(k.h - k.l, abs(k.h - p.c), abs(k.l - p.c)))
    return sum(trs) / len(trs)


def swing_highs(candles: Sequence[Candle], n: int = 2) -> list[int]:
    out = []
    for i in range(n, len(candles) - n):
        h = candles[i].h
        if all(h > candles[i - j].h and h >= candles[i + j].h for j in range(1, n + 1)):
            out.append(i)
    return out


def swing_lows(candles: Sequence[Candle], n: int = 2) -> list[int]:
    out = []
    for i in range(n, len(candles) - n):
        lo = candles[i].l
        if all(lo < candles[i - j].l and lo <= candles[i + j].l for j in range(1, n + 1)):
            out.append(i)
    return out


@dataclass(frozen=True)
class Gap:
    """Имбаланс (FVG). lo/hi — границы незакрытого разрыва цены."""

    bullish: bool  # True: разрыв вверх (снизу зона, цена ушла вверх)
    lo: float
    hi: float
    idx: int  # индекс третьей свечи
    t: int
    tf: str = ""

    @property
    def size(self) -> float:
        return self.hi - self.lo


def fvg_gaps(candles: Sequence[Candle], params: Params, tf: str = "", only_open: bool = True) -> list[Gap]:
    """FVG размером не меньше big_imbalance. only_open — исключить полностью закрытые."""
    min_size = params.px(params.big_imbalance)
    gaps: list[Gap] = []
    for i in range(2, len(candles)):
        a, c = candles[i - 2], candles[i]
        if c.l - a.h >= min_size:
            gap = Gap(True, a.h, c.l, i, c.t, tf)
        elif a.l - c.h >= min_size:
            gap = Gap(False, c.h, a.l, i, c.t, tf)
        else:
            continue
        if only_open:
            lo, hi = gap.lo, gap.hi
            filled = False
            for k in candles[i + 1 :]:
                if k.l <= lo and k.h >= hi:
                    filled = True
                    break
            if filled:
                continue
        gaps.append(gap)
    return gaps


def price_in_gap(price: float, gap: Gap) -> bool:
    return gap.lo <= price <= gap.hi


def side_of_move(side: Side) -> int:
    return side.sign
