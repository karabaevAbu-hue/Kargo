"""Major-ликвидность (потенциальные LAL / цели для тейков).

Major: Doji (не AFU), идеальные двойные вершины/дно, трендовая линия с 3+ касаниями,
свечи с имбалансом (FVG) на M5 и старше. Minor (AFU, грязные уровни, линии с <3
касаниями, сбалансированные свечи) целью не считается и сюда не попадает.

Цели ищутся в бычьей форме (над ценой). Для SELL свечи зеркалятся.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from ..models import Candle, Side, mirror_all
from .candles import fvg_gaps, swing_highs
from .params import Params
from .patterns import is_doji_not_afu

KIND_RU = {
    "DOJI": "хай Doji" ,
    "DOJI_LOW": "лой Doji",
    "DOUBLE": "равные хаи",
    "DOUBLE_LOW": "равные лои",
    "TRENDLINE": "трендовая линия 3+ касаний",
    "IMBALANCE": "имбаланс (FVG)",
}

# ТФ для каждого типа цели (выбор реализации: шумные M15 и ниже для Doji не берём).
LEVEL_TFS = ("H1", "H4")
GAP_TFS = ("M5", "M15", "M30", "H1", "H4")


@dataclass(frozen=True)
class Target:
    price: float
    kind: str
    tf: str
    t: int

    def label(self, side: Side) -> str:
        kind = self.kind
        if side is Side.SELL and kind in ("DOJI", "DOUBLE"):
            kind += "_LOW"
        return f"{KIND_RU.get(kind, kind)} {self.tf}"


def _dojis_up(c: Sequence[Candle], tf: str, p: Params) -> list[Target]:
    out = []
    for i in range(1, len(c)):
        if is_doji_not_afu(c, i, p) and all(k.h < c[i].h for k in c[i + 1 :]):
            out.append(Target(c[i].h, "DOJI", tf, c[i].t))
    return out


def _doubles_up(c: Sequence[Candle], tf: str, p: Params) -> list[Target]:
    """Идеальные двойные вершины: ровные хаи (разница <= perfect_double_tol)."""
    tol = p.px(p.perfect_double_tol)
    sw = swing_highs(c, 2)[-8:]
    out = []
    for a in range(len(sw)):
        for b in range(a + 1, len(sw)):
            ia, ib = sw[a], sw[b]
            ha, hb = c[ia].h, c[ib].h
            if ib - ia < 3 or abs(ha - hb) > tol:
                continue
            lvl = max(ha, hb)
            if any(k.h > lvl + tol for k in c[ia : ib + 1]):
                continue
            if any(k.h >= lvl for k in c[ib + 1 :]):
                continue
            out.append(Target(lvl, "DOUBLE", tf, c[ib].t))
    return out


def _trendlines_up(c: Sequence[Candle], tf: str, p: Params) -> list[Target]:
    """Идеальные линии: >= 3 касаний хаёв, ни одна свеча между касаниями линию не пробивает."""
    tol = p.px(p.trendline_tol)
    sw = swing_highs(c, 2)[-6:]
    out: list[Target] = []
    seen: list[float] = []
    n = len(c)
    for a in range(len(sw)):
        for b in range(a + 1, len(sw)):
            ia, ib = sw[a], sw[b]
            slope = (c[ib].h - c[ia].h) / (ib - ia)
            touches = [j for j in sw if abs(c[ia].h + slope * (j - ia) - c[j].h) <= tol]
            if len(touches) < 3:
                continue
            first, last = touches[0], touches[-1]
            if any(c[k].h > c[ia].h + slope * (k - ia) + tol for k in range(first, last + 1)):
                continue
            value = c[ia].h + slope * (n - ia)
            if any(abs(value - s) < p.px(0.5) for s in seen):
                continue
            seen.append(value)
            out.append(Target(value, "TRENDLINE", tf, c[last].t))
    return out


def _gaps_up(c: Sequence[Candle], tf: str, p: Params) -> list[Target]:
    """Медвежьи FVG выше цены: ближний край, пока цена его не касалась."""
    out = []
    for g in fvg_gaps(c, p, tf, only_open=True):
        if g.bullish:
            continue
        if all(k.h < g.lo for k in c[g.idx + 1 :]):
            out.append(Target(g.lo, "IMBALANCE", tf, g.t))
    return out


def targets_for_tf(side: Side, candles: Sequence[Candle], tf: str, p: Params) -> list[Target]:
    mirror = side is Side.SELL
    c = mirror_all(candles) if mirror else list(candles)
    out: list[Target] = []
    if tf in LEVEL_TFS or tf in ("D1", "W"):
        out += _dojis_up(c, tf, p) + _doubles_up(c, tf, p) + _trendlines_up(c, tf, p)
    if tf in GAP_TFS or tf in ("D1", "W"):
        out += _gaps_up(c, tf, p)
    if mirror:
        out = [Target(-t.price, t.kind, t.tf, t.t) for t in out]
    return out


def find_major_targets(
    side: Side,
    frames: Mapping[str, Sequence[Candle]],
    p: Params,
    beyond: float,
    tfs: Sequence[str] | None = None,
) -> list[Target]:
    """Major-цели в сторону сделки дальше уровня beyond, от ближней к дальней."""
    tfs = tfs or tuple(dict.fromkeys((*GAP_TFS, *LEVEL_TFS)))
    found: list[Target] = []
    for tf in tfs:
        c = frames.get(tf)
        if not c:
            continue
        found += targets_for_tf(side, c, tf, p)
    if side is Side.BUY:
        found = [t for t in found if t.price > beyond]
        found.sort(key=lambda t: t.price)
    else:
        found = [t for t in found if t.price < beyond]
        found.sort(key=lambda t: -t.price)
    dedup: list[Target] = []
    for t in found:
        if dedup and abs(t.price - dedup[-1].price) < p.px(0.3):
            continue
        dedup.append(t)
    return dedup
