"""Помощники для тестов: сборка свечей из кортежей."""
from xau_engine.models import Candle


def mk(rows, t0=0, step=300):
    return [Candle(t0 + i * step, *r) for i, r in enumerate(rows)]


def flat(n, price=100.0, t0=0, step=300, amp=0.2):
    """n почти плоских свечей вокруг price (без паттернов)."""
    out = []
    for i in range(n):
        up = i % 2 == 0
        o = price - amp if up else price + amp
        c = price + amp if up else price - amp
        out.append(Candle(t0 + i * step, o, max(o, c) + 0.05, min(o, c) - 0.05, c))
    return out
