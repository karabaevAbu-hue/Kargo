"""Манипуляции внутри зоны OB: FU, Negation, FU re-test, HCS (и их кратность x2/x3)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from ..models import Candle, Side
from .params import Params
from .patterns import (
    OrderBlock,
    detect_fu,
    detect_negation,
    find_retests,
)

SCAN_TFS = ("H1", "M30", "M15", "M5")
WINDOW = {"H1": 120, "M30": 150, "M15": 200, "M5": 400}


@dataclass(frozen=True)
class Manip:
    kind: str  # FU | NEG | RETEST | HCS
    tf: str
    t: int
    side: Side
    tip: float  # кончик тени (самый глубокий вынос)
    level: float  # забранный уровень (Prev LOW / Prev HIGH)
    edge: float  # граница тени в сторону цены
    rank: int = 8  # для FU: тип 1..9
    strong: bool = False
    hcs_count: int = 0  # для HCS: сколько HCS подряд на этой цепочке
    inside_ob: bool = False  # тень пересекает тело OB без допуска
    detail: str = ""

    @property
    def region(self) -> tuple[float, float]:
        vals = (self.tip, self.level, self.edge)
        return min(vals), max(vals)


def _overlaps(r: tuple[float, float], lo: float, hi: float) -> bool:
    return r[0] <= hi and r[1] >= lo


def collect_manipulations(
    side: Side,
    ob: OrderBlock,
    frames: Mapping[str, Sequence[Candle]],
    p: Params,
) -> list[Manip]:
    """Все манипуляции в сторону side после образования OB, чья тень лежит у тела OB."""
    tol = p.px(p.zone_target)  # манипуляция должна лежать у самого тела OB
    lo_t, hi_t = ob.lo - tol, ob.hi + tol
    out: list[Manip] = []

    def add(m: Manip) -> None:
        if _overlaps(m.region, lo_t, hi_t):
            inside = _overlaps(m.region, ob.lo, ob.hi)
            out.append(
                Manip(**{**m.__dict__, "inside_ob": inside})  # type: ignore[arg-type]
            )

    # FU, образовавший зону на ТФ OB
    f = ob.fu
    add(Manip("FU", ob.tf, f.t, side, f.tip, f.ref, f.wick_hi if side is Side.BUY else f.wick_lo,
              f.rank, f.strong, 0, False, f.kind))

    for tf in SCAN_TFS:
        series = frames.get(tf)
        if not series:
            continue
        n = len(series)
        first = max(1, n - WINDOW[tf])
        for i in range(first, n):
            if series[i].t < ob.fu.t:
                continue
            fu = detect_fu(series, i, side, p)
            if fu:
                edge = fu.wick_hi if side is Side.BUY else fu.wick_lo
                add(Manip("FU", tf, fu.t, side, fu.tip, fu.ref, edge, fu.rank, fu.strong, 0, False, fu.kind))
                retests = find_retests(series, fu, p)
                hcs = [r for r in retests if r.kind == "HCS"]
                for r in retests:
                    add(
                        Manip(
                            r.kind, tf, r.t, side, r.tip, r.level, r.edge,
                            r.fu.rank if r.fu else 8, bool(r.fu and r.fu.strong),
                            len(hcs) if r.kind == "HCS" else 0, False,
                            r.fu.kind if r.fu else "FU re-test",
                        )
                    )
            neg = detect_negation(series, i, side, p)
            if neg:
                add(Manip("NEG", tf, neg.t, side, neg.tip, neg.level, neg.edge, 0, neg.strong, 0, False,
                          "Negation x3" if neg.triple else "Negation"))
    return _dedupe(out)


def _dedupe(events: list[Manip]) -> list[Manip]:
    """Убирает точные повторы (одна и та же свеча попала в список дважды).

    Одна свеча, увиденная на разных ТФ, остаётся с разными уровнями: младший ТФ уточняет
    True Stop. Счёт «разных манипуляций» ведёт зона по кончикам теней.
    """
    best: dict[tuple, Manip] = {}
    for m in events:
        key = (m.kind, m.tf, m.t)
        cur = best.get(key)
        if cur is None or m.hcs_count > cur.hcs_count:
            best[key] = m
    return sorted(best.values(), key=lambda m: m.t)
