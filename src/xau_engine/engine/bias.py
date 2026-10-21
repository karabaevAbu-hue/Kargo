"""Bias: направление потока старшего ТФ (W, D1, H4). Старший ТФ сильнее младшего."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..models import Candle, Side
from .candles import atr, swing_highs, swing_lows
from .params import Params
from .patterns import Negation, detect_negation

WEIGHTS = {"W": 3, "D1": 2, "H4": 1}
NEG_LOOKBACK = 3  # Negation считается свежей, если на одной из последних свечей ТФ


@dataclass
class Bias:
    side: Side | None
    votes: dict[str, int] = field(default_factory=dict)
    negation: tuple[str, Negation] | None = None  # какой ТФ развернул bias

    def describe(self) -> str:
        if self.side is None:
            return "нет"
        word = "бычий" if self.side is Side.BUY else "медвежий"
        if self.negation:
            return f"{word} (Negation {self.negation[0]})"
        return word


def tf_trend(candles: Sequence[Candle], p: Params) -> int:
    """+1 / -1 / 0: структура свингов, при неясности — направление последнего импульса."""
    if len(candles) < 6:
        return 0
    sh = swing_highs(candles, 2)[-2:]
    sl = swing_lows(candles, 2)[-2:]
    if len(sh) == 2 and len(sl) == 2:
        hh = candles[sh[1]].h > candles[sh[0]].h
        hl = candles[sl[1]].l > candles[sl[0]].l
        if hh and hl:
            return 1
        if not hh and not hl:
            return -1
    a = atr(candles, 14)
    move = candles[-1].c - candles[-min(10, len(candles))].c
    if abs(move) >= a:
        return 1 if move > 0 else -1
    return 0


def recent_negation(candles: Sequence[Candle], p: Params) -> Negation | None:
    n = len(candles)
    for i in range(n - 1, max(1, n - NEG_LOOKBACK) - 1, -1):
        for side in (Side.BUY, Side.SELL):
            neg = detect_negation(candles, i, side, p)
            if neg:
                return neg
    return None


def compute_bias(frames: Mapping[str, Sequence[Candle]], p: Params) -> Bias:
    votes: dict[str, int] = {}
    for tf in WEIGHTS:
        c = frames.get(tf)
        if c:
            votes[tf] = tf_trend(c, p)
    # Negation на HTF меняет bias на противоположный; старший ТФ проверяется первым.
    for tf in WEIGHTS:
        c = frames.get(tf)
        if not c:
            continue
        neg = recent_negation(c, p)
        if neg:
            return Bias(neg.side, votes, (tf, neg))
    total = sum(WEIGHTS[tf] * v for tf, v in votes.items())
    if total >= 2:
        return Bias(Side.BUY, votes)
    if total <= -2:
        return Bias(Side.SELL, votes)
    return Bias(None, votes)
