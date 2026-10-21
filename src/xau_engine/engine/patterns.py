"""Паттерны стратегии Mr. Casino: Doji, FU (SFU/AFU/BIW), Negation, odd-свечи (OB),
FU re-test, HCS и уровни ретеста HTF-пары.

Медвежья и бычья схемы зеркальны. Базовый код пишется для одной стороны,
для другой свечи зеркалятся (p -> -p), поэтому логика в обе стороны одна и та же.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..models import Candle, Side, mirror_all
from .candles import atr
from .params import Params

# --------------------------------------------------------------------------- Doji


def is_small_body(c: Candle, ratio: float) -> bool:
    return c.range == 0 or c.body <= ratio * c.range


def inside_body(c: Candle, other: Candle) -> bool:
    return c.h <= other.body_hi and c.l >= other.body_lo


def is_doji(candles: Sequence[Candle], i: int, p: Params) -> bool:
    """Doji: малое тело, либо свеча целиком внутри тела соседней (100% Doji)."""
    c = candles[i]
    if is_small_body(c, p.doji_body_ratio):
        return True
    if i > 0 and inside_body(c, candles[i - 1]):
        return True
    return i + 1 < len(candles) and inside_body(c, candles[i + 1])


def is_doji_not_afu(candles: Sequence[Candle], i: int, p: Params) -> bool:
    """Major-Doji: не берёт ликвидность (не выходит за экстремумы предыдущей свечи)."""
    if i < 1 or not is_doji(candles, i, p):
        return False
    c, prev = candles[i], candles[i - 1]
    return c.h <= prev.h and c.l >= prev.l


# --------------------------------------------------------------------------- FU

FU_KINDS = {
    1: "SFU-BIW",
    2: "SFU-ENGULF",
    3: "AFU-OVERLAP+WICK",
    4: "AFU-OVERLAP",
    5: "AFU-WICK",
    6: "AFU-REVERSED",
    7: "AFU-BIW",
    8: "FU",
}


@dataclass(frozen=True)
class FU:
    idx: int
    t: int
    side: Side  # BUY: бычий FU (забрал ликвидность снизу и развернулся вверх)
    rank: int  # 1..7 — типы со слайда, 8 — обычный FU, 9 — трёхсвечный
    kind: str
    tip: float  # кончик тени
    ref: float  # уровень, который FU забрал
    wick_lo: float
    wick_hi: float  # границы тени FU
    biw: bool  # тело внутри тени предыдущей свечи
    prev_doji: bool = False

    @property
    def strong(self) -> bool:
        return self.rank <= 2


def _classify_bear(a: Candle, b: Candle, p: Params) -> tuple[int, bool] | None:
    """Медвежья форма: b забирает хай a и закрывается обратно под ним."""
    if not (b.h > a.h and b.c < a.h):
        return None
    rng = b.range
    a_lo, a_hi = a.body_lo, a.body_hi
    biw = b.body_hi <= a_lo
    strong_body = rng > 0 and b.body >= p.strong_body_ratio * rng
    wick = rng > 0 and b.upper_wick >= p.big_wick_ratio * rng
    engulf = b.bear and b.body_hi >= a_hi and b.body_lo <= a_lo
    overlapped = b.bear and b.c <= (a_hi + a_lo) / 2
    reversed_color = b.bear and a.bull
    if biw and b.bear and strong_body:
        return 1, biw
    if engulf:
        return 2, biw
    if overlapped and wick:
        return 3, biw
    if overlapped:
        return 4, biw
    if wick:
        return 5, biw
    if reversed_color:
        return 6, biw
    if biw:
        return 7, biw
    return 8, biw


def detect_fu(candles: Sequence[Candle], i: int, side: Side, p: Params) -> FU | None:
    """FU на свече i в сторону side (BUY — забрана ликвидность снизу)."""
    if i < 1 or i >= len(candles):
        return None
    mirror = side is Side.BUY
    b = candles[i].mirrored() if mirror else candles[i]
    a = candles[i - 1].mirrored() if mirror else candles[i - 1]
    res = _classify_bear(a, b, p)
    ref = a.h
    rank = 0
    biw = False
    if res is not None:
        rank, biw = res
    elif i >= 2:  # 3 Candle FU: забирает экстремум двух предыдущих свечей
        a2 = candles[i - 2].mirrored() if mirror else candles[i - 2]
        level = max(a.h, a2.h)
        if b.h > level and b.c < level:
            rank, ref = 9, level
    if rank == 0:
        return None
    if mirror:
        tip, ref_o = -b.h, -ref
        wick_lo, wick_hi = -b.h, -b.body_hi
    else:
        tip, ref_o = b.h, ref
        wick_lo, wick_hi = b.body_hi, b.h
    kind = FU_KINDS.get(rank, "FU3")
    return FU(
        idx=i,
        t=candles[i].t,
        side=side,
        rank=rank,
        kind=kind,
        tip=tip,
        ref=ref_o,
        wick_lo=wick_lo,
        wick_hi=wick_hi,
        biw=biw,
        prev_doji=is_small_body(candles[i - 1], p.doji_body_ratio),
    )


# --------------------------------------------------------------------------- Negation


@dataclass(frozen=True)
class Negation:
    idx: int
    t: int
    side: Side  # направление разворота
    strong: bool
    tip: float
    level: float  # уровень, который отрицающая свеча забрала
    edge: float  # граница тени в сторону цены
    triple: bool = False


def detect_negation(candles: Sequence[Candle], i: int, side: Side, p: Params) -> Negation | None:
    """Negation на свече i. side — направление разворота (BUY: Bearish 2 Bullish)."""
    if i < 2 or i >= len(candles):
        return None
    mirror = side is Side.SELL
    n0, n1, n2 = candles[i - 2], candles[i - 1], candles[i]
    if mirror:
        n0, n1, n2 = n0.mirrored(), n1.mirrored(), n2.mirrored()
    # N1 — медвежья свеча, забравшая ликвидность снизу
    if not (n1.bear and n1.l < n0.l):
        return None
    # N2 — бычья свеча со своим FU вниз, закрывается выше открытия N1
    if not (n2.bull and n2.l < n1.l and n2.c > n1.o):
        return None
    n1_wick = n1.lower_wick / n1.range if n1.range else 0.0
    n2_wick = n2.lower_wick / n2.range if n2.range else 0.0
    engulf = n2.o <= n1.c and n2.c >= n1.o
    strong = engulf and n1_wick <= 0.25 and n2_wick >= p.big_wick_ratio
    tip, level, edge = n2.l, n1.l, n2.body_lo
    if mirror:
        tip, level, edge = -tip, -level, -edge
    triple = detect_negation(candles, i - 1, side.opposite, p) is not None if i >= 3 else False
    return Negation(i, candles[i].t, side, strong, tip, level, edge, triple)


# --------------------------------------------------------------------------- Order Block


@dataclass(frozen=True)
class OrderBlock:
    tf: str
    side: Side  # сторона сделки: BUY для Sell to Buy
    idx: int
    t: int
    lo: float  # POI: только тело свечи
    hi: float
    full_lo: float
    full_hi: float
    fu: FU
    label: str

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2


def _odd_candle(candles: Sequence[Candle], k: int, side: Side, p: Params) -> bool:
    """Odd-свеча для стороны side: одиночная контр-свеча, после которой начинается движение."""
    if k < 1 or k + 1 >= len(candles):
        return False
    mirror = side is Side.SELL
    seq = [candles[x].mirrored() if mirror else candles[x] for x in range(max(0, k - 14), min(len(candles), k + 6))]
    kk = k - max(0, k - 14)
    prev, odd, nxt = seq[kk - 1], seq[kk], seq[kk + 1]
    # BUY-форма: odd — медвежья свеча (Sell to Buy) среди бычьего движения
    if not (odd.bear and prev.bull and nxt.bull):
        return False
    if odd.range == 0 or (odd.upper_wick + odd.lower_wick) > p.ob_max_wick_ratio * odd.range:
        return False
    if nxt.c <= odd.body_hi:
        return False
    a = atr([*seq[: kk + 1]], 14)
    push = max(x.h for x in seq[kk + 1 : kk + 6]) - odd.body_hi
    return push >= p.displacement_atr * a


def find_order_blocks(
    candles: Sequence[Candle], tf: str, p: Params, lookback: int = 60
) -> list[OrderBlock]:
    """OB на ТФ (Weekly..1H): odd-свеча + следующий за ней FU, зона не пробита закрытием."""
    out: list[OrderBlock] = []
    n = len(candles)
    start = max(1, n - lookback)
    for k in range(start, n - 1):
        for side in (Side.BUY, Side.SELL):
            if not _odd_candle(candles, k, side, p):
                continue
            c = candles[k]
            fu = None
            for j in range(k + 1, min(k + 5, n)):
                fu = detect_fu(candles, j, side, p)
                if fu:
                    break
            if fu is None:
                continue
            lo, hi = c.body_lo, c.body_hi
            broken = any(
                (x.c < lo) if side is Side.BUY else (x.c > hi) for x in candles[k + 1 :]
            )
            if broken:
                continue
            label = "Sell to Buy" if side is Side.BUY else "Buy to Sell"
            out.append(OrderBlock(tf, side, k, c.t, lo, hi, c.l, c.h, fu, label))
    out.sort(key=lambda o: o.idx, reverse=True)
    return out


# --------------------------------------------------------------------------- FU re-test / HCS


@dataclass(frozen=True)
class ReTest:
    idx: int
    t: int
    kind: str  # RETEST — FU re-test, HCS — FU, перетестированный FU
    side: Side
    anchor_idx: int
    tip: float
    level: float
    edge: float
    fu: FU | None = None


def find_retests(candles: Sequence[Candle], anchor: FU, p: Params) -> list[ReTest]:
    """Возвраты цены в зону тени FU с отбоем. Возврат свечой-FU — это HCS."""
    side = anchor.side
    mirror = side is Side.SELL
    seq = mirror_all(candles) if mirror else list(candles)
    z_lo, z_hi = (-anchor.wick_hi, -anchor.wick_lo) if mirror else (anchor.wick_lo, anchor.wick_hi)
    width = max(z_hi - z_lo, p.px(0.1))
    tol = 0.5 * width
    out: list[ReTest] = []
    left = False
    for j in range(anchor.idx + 1, len(seq)):
        k = seq[j]
        if not left:
            if k.l > z_hi:
                left = True
            elif k.c < z_lo - tol:
                break
            continue
        if k.c < z_lo or k.l < z_lo - tol:
            break
        if k.l <= z_hi and k.c >= z_hi:
            fu = detect_fu(candles, j, side, p)
            c = candles[j]
            tip = c.l if side is Side.BUY else c.h
            edge = c.body_lo if side is Side.BUY else c.body_hi
            level = fu.ref if fu else anchor.ref
            out.append(
                ReTest(j, c.t, "HCS" if fu else "RETEST", side, anchor.idx, tip, level, edge, fu)
            )
            left = False
    return out


# --------------------------------------------------------------------------- HTF-пара Doji + большая свеча


@dataclass(frozen=True)
class RetestPair:
    tf: str
    idx: int  # индекс большой свечи
    t: int
    side: Side  # сценарий: SELL — цена возвращается вверх к уровням
    levels: tuple[float, float, float, float]  # Level #1..#4 в порядке подхода цены

    def broken(self, candles: Sequence[Candle]) -> bool:
        """Level #4 пробит закрытием — сценарий больше не интересен."""
        l4 = self.levels[3]
        for c in candles[self.idx + 1 :]:
            if (c.c > l4) if self.side is Side.SELL else (c.c < l4):
                return True
        return False


def find_retest_pairs(
    candles: Sequence[Candle], tf: str, side: Side, p: Params, lookback: int = 40
) -> list[RetestPair]:
    mirror = side is Side.BUY
    out: list[RetestPair] = []
    n = len(candles)
    for j in range(max(1, n - lookback), n - 1):
        d = candles[j]
        x = candles[j + 1]
        if not is_doji(candles, j, p):
            continue
        dd, xx = (d.mirrored(), x.mirrored()) if mirror else (d, x)
        a = atr(candles[max(0, j - 13) : j + 2], 14)
        if not (xx.bear and xx.h > dd.h and xx.range >= a):
            continue
        l1, l2, l3 = dd.l, (dd.l + dd.h) / 2, dd.h
        l4 = (xx.body_hi + xx.h) / 2
        if l4 <= l3:
            continue
        levels = (l1, l2, l3, l4)
        if mirror:
            levels = tuple(-v for v in levels)  # type: ignore[assignment]
        out.append(RetestPair(tf, j + 1, x.t, side, levels))  # type: ignore[arg-type]
    out.sort(key=lambda r: r.idx, reverse=True)
    return out
