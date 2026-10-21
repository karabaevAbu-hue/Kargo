"""Синтетический сценарий BUY для сквозных тестов анализатора.

История M5 собирается из баров заданного OHLC (путь o->l->h->c для бычьих, o->h->l->c для медвежьих),
поэтому агрегаты M15/H1/H4 точно совпадают с авторскими. SELL-сценарий — зеркало цен.
"""
import random

from xau_engine.engine.params import Params
from xau_engine.models import Candle

T0 = (1_700_000_000 // 14400) * 14400  # кратно 4 часам
P = Params(day_open_utc_hour=0)


def render(o, h, l, c, n, t):
    """n свечей M5, агрегат которых равен (o,h,l,c)."""
    pts = [o, l, h, c] if c >= o else [o, h, l, c]
    idx = [0, n // 3, (2 * n) // 3, n]
    closes = []
    for s in range(3):
        a, b = idx[s], idx[s + 1]
        for k in range(a + 1, b + 1):
            closes.append(pts[s] + (pts[s + 1] - pts[s]) * (k - a) / (b - a))
    out, prev = [], o
    for i, cl in enumerate(closes):
        out.append(Candle(t + i * 300, prev, max(prev, cl), min(prev, cl), cl))
        prev = cl
    return out


class Builder:
    def __init__(self):
        self.m5: list[Candle] = []
        self.t = T0
        self.last = None

    def bar(self, o, h, l, c, n=12):
        if self.last is not None:
            assert abs(o - self.last) < 1e-9, f"разрыв: {o} != {self.last}"
        self.m5 += render(o, h, l, c, n, self.t)
        self.t += n * 300
        self.last = c

    def h1(self, o, h, l, c):
        self.bar(o, h, l, c, 12)

    def m15(self, o, h, l, c):
        self.bar(o, h, l, c, 3)


def descend(b: Builder, start, count, step, seed=1):
    rnd = random.Random(seed)
    px = start
    for _ in range(count):
        c = px - step + rnd.uniform(-0.6, 0.6)
        hi = max(px, c) + rnd.uniform(0.3, 1.0)
        lo = min(px, c) - rnd.uniform(0.3, 1.0)
        b.h1(px, hi, lo, c)
        px = c
    return px


def uptrend_frame(n, start, step, period):
    """Дневной/недельный ряд с растущими хаями и лоями (bias BUY)."""
    out, px = [], start
    for i in range(n):
        o = px
        c = px + step
        out.append(Candle(T0 - (n - i) * period, o, c + step * 0.2, o - step * 0.2, c))
        px = c
        if i % 3 == 2:  # откат, чтобы были свинги
            o, c = px, px - step * 0.5
            px = c
    return out


def build_buy():
    b = Builder()
    # ---- история: снижение от 4135, внутри Doji B (high 4127, не взят позже)
    px = descend(b, 4135.0, 12, 0.6, seed=3)
    b.h1(px, 4128.3, 4123.9, 4124.2)         # большая свеча-контейнер
    b.h1(4124.2, 4127.0, 4125.0 - 0.0, 4124.25)  # Doji B: тело ~0, внутри предыдущей свечи по диапазону
    b.last = 4124.25
    px = 4124.25
    # снижение к 4096 без новых хаёв выше 4127
    rnd = random.Random(7)
    while px > 4097:
        c = max(px - 1.1 + rnd.uniform(-0.4, 0.4), 4096.0)
        b.h1(px, px + rnd.uniform(0.3, 0.8), c - rnd.uniform(0.3, 0.8), c)
        px = c
    b.h1(px, 4102.0, px - 0.5, 4101.5) if px < 4101 else None
    px = b.last
    # подвести цену к 4096.0 для старта OB
    if abs(px - 4096.0) > 1e-9:
        b.h1(px, max(px, 4096.0) + 0.3, min(px, 4096.0) - 0.3, 4096.0)
    # ---- OB (Sell to Buy): k-1 бычья, k — odd, k+1 — бычий FU с выносом ниже low odd
    b.h1(4096.0, 4102.0, 4095.5, 4101.5)
    b.h1(4101.5, 4101.6, 4100.5, 4100.6)      # odd: тело 4100.6..4101.5, почти без теней
    b.h1(4100.6, 4109.0, 4096.8, 4108.5)      # FU вниз к 4096.8 и выход вверх
    # ---- ралли и Doji A (high 4119 — TP1)
    b.h1(4108.5, 4113.5, 4108.2, 4113.0)
    b.h1(4113.0, 4119.4, 4112.8, 4118.0)
    b.h1(4118.0, 4119.0, 4117.0, 4118.1)      # Doji A, high 4119.0
    # ---- откат к 4106
    # медленный откат (дрейф < 3 пунктов за бар), чтобы не рисовать FVG
    for o, c in ((4118.1, 4115.8), (4115.8, 4113.4), (4113.4, 4111.0), (4111.0, 4108.6), (4108.6, 4106.0)):
        b.h1(o, o + 0.1, c - 0.1, c)
    # ---- час подтверждения: FU (E1) и выход вверх, E2 — HCS, затем удержание выше зоны
    b.m15(4106.0, 4107.0, 4100.0, 4103.0)     # Prev LOW = 4100.0
    b.m15(4103.0, 4107.5, 4096.2, 4106.0)     # E1: FU, вынос до 4096.2
    b.m15(4106.0, 4112.0, 4105.5, 4111.0)     # уход вверх от зоны
    b.m15(4111.0, 4111.5, 4103.8, 4108.0)
    b.m15(4108.0, 4109.0, 4101.5, 4108.5)     # E2: возврат в тень FU свечой-FU = HCS
    b.m15(4108.5, 4110.0, 4107.5, 4109.0)
    b.m15(4109.0, 4110.5, 4108.0, 4109.5)
    b.m15(4109.5, 4110.0, 4108.8, 4109.2)

    m5 = b.m15 and b.m5
    d1 = uptrend_frame(40, 3900.0, 6.0, 86400)
    w = uptrend_frame(30, 3500.0, 25.0, 604800)
    return m5, d1, w


def mirror(m5, d1, w):
    f = lambda xs: [c.mirrored() for c in xs]
    return f(m5), f(d1), f(w)
