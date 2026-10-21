"""Зона входа 5-10 пунктов по правилу True Stop, стоп и тейки."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..models import Candle, Side
from .liquidity import Target, find_major_targets
from .manipulation import Manip
from .params import Params


@dataclass
class Zone:
    side: Side
    lo: float
    hi: float
    true_stop: float  # Prev LOW (BUY) / Prev HIGH (SELL): уровень, где сошлись манипуляции
    sl: float
    members: list[Manip] = field(default_factory=list)
    widened: bool = False  # пришлось расширить до zone_max

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2

    def width_pts(self, p: Params) -> float:
        return p.pts(self.hi - self.lo)


@dataclass
class ZoneFail:
    reason: str  # wide | no_cluster | sl_far | no_overlap


def _distinct(events: Sequence[Manip]) -> int:
    """Сколько разных манипуляций: определяются кончиком тени."""
    return len({round(e.tip, 1) for e in events})


def _build_buy(events: Sequence[Manip], ob_lo: float, ob_hi: float, p: Params):
    """Бычья форма. Возвращает (level, width, members, widened) либо ZoneFail."""
    if _distinct(events) < 2:
        return ZoneFail("no_cluster")
    for widened, width in ((False, p.px(p.zone_target)), (True, p.px(p.zone_max))):
        best_c, best = None, []
        for e0 in events:
            c = e0.level
            inc = [e for e in events if c - 1e-9 <= e.level <= c + width + 1e-9]
            n_inc, n_best = _distinct(inc), _distinct(best)
            if n_inc > n_best or (n_inc == n_best and best_c is not None and c < best_c):
                best_c, best = c, inc
        if best_c is not None and _distinct(best) >= 2:
            c = best_c
            ext = max(max(e.level, e.edge) for e in best) - c
            w = min(max(ext, p.px(p.min_zone_width)), width)
            if c > ob_hi or c + w < ob_lo:
                continue
            return c, w, best, widened
    return ZoneFail("wide")


def build_zone(
    side: Side, events: Sequence[Manip], ob_lo: float, ob_hi: float, p: Params
) -> Zone | ZoneFail:
    """Шаги 3-5 ТЗ: уточнить зону до уровня нескольких манипуляций и посчитать стоп."""
    if side is Side.BUY:
        ev, lo, hi = list(events), ob_lo, ob_hi
    else:
        mir = lambda m: Manip(**{**m.__dict__, "tip": -m.tip, "level": -m.level, "edge": -m.edge})  # noqa: E731
        ev, lo, hi = [mir(m) for m in events], -ob_hi, -ob_lo
    res = _build_buy(ev, lo, hi, p)
    if isinstance(res, ZoneFail):
        return res
    c, w, members, widened = res
    sl = min(min(e.tip for e in members), c) - p.px(p.sl_buffer)
    zl, zh = c, c + w
    if side is Side.SELL:
        zl, zh, c, sl = -zh, -zl, -c, -sl
    orig = [m for m in events if any(m.t == x.t and m.kind == x.kind and m.tf == x.tf for x in members)]
    zone = Zone(side, zl, zh, c, sl, orig, widened)
    risk = abs(zone.mid - sl)
    if risk > p.px(p.sl_max):
        return ZoneFail("sl_far")
    return zone


@dataclass
class Plan:
    tps: list[Target]
    risk: float  # от середины зоны до SL, цена
    rr: list[float]  # RR для каждого TP
    tp3_note: str = ""


def build_plan(
    zone: Zone,
    frames: Mapping[str, Sequence[Candle]],
    htf_fu_edge: float | None,
    p: Params,
) -> Plan | None:
    """TP1 — ближайшая major LAL, TP2 — следующая, TP3 — край FU старшего ТФ или цель HTF."""
    side = zone.side
    beyond = zone.hi if side is Side.BUY else zone.lo
    targets = find_major_targets(side, frames, p, beyond)
    if not targets:
        return None
    sign = side.sign
    min_gap = p.px(1.0)
    tp1 = targets[0]
    tps = [tp1]
    tp2 = next((t for t in targets[1:] if (t.price - tp1.price) * sign >= min_gap), None)
    if tp2:
        tps.append(tp2)
    note = ""
    last = tps[-1].price
    # TP3: верх/низ FU старшего ТФ (точка 4 цикла) либо цель HTF
    if tp2 is not None:
        if htf_fu_edge is not None and (htf_fu_edge - last) * sign >= min_gap:
            tps.append(Target(htf_fu_edge, "FU_EDGE", "HTF", 0))
            note = "край FU старшего ТФ"
        else:
            htf = find_major_targets(side, frames, p, last + sign * min_gap, tfs=("H4", "D1", "W"))
            if htf:
                tps.append(htf[0])
                note = "цель HTF"
    risk = abs(zone.mid - zone.sl)
    if risk <= 0:
        return None
    rr = [abs(t.price - zone.mid) / risk for t in tps]
    return Plan(tps, risk, rr, note)
