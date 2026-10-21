"""Статус зоны OB: NEW/FRESH или CONFIRMED (цена ушла, вернулась и отреагировала тенью)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from ..models import Candle, Side, mirror_all
from .params import Params
from .patterns import OrderBlock, detect_fu


@dataclass(frozen=True)
class ZoneStatus:
    status: str  # NEW | CONFIRMED
    left_t: int | None = None
    confirm_t: int | None = None
    confirm_tf: str | None = None


def _scan_buy(series: Sequence[Candle], start_t: int, lo: float, hi: float, tol: float, p: Params):
    """Бычья форма. Возвращает (left_t, confirm_t)."""
    left_t = None
    for i, k in enumerate(series):
        if k.t <= start_t:
            continue
        if left_t is None:
            if k.l > hi:
                left_t = k.t
            continue
        if k.c < lo:
            return left_t, None
        if k.l <= hi and k.l >= lo - tol and k.c >= lo:
            wick = k.range > 0 and k.lower_wick >= p.big_wick_ratio * k.range
            if wick or detect_fu(series, i, Side.BUY, p):
                return left_t, k.t
    return left_t, None


def zone_status(ob: OrderBlock, frames: Mapping[str, Sequence[Candle]], p: Params) -> ZoneStatus:
    tol = p.px(p.zone_max)
    best: ZoneStatus | None = None
    left_any: int | None = None
    for tf in dict.fromkeys((ob.tf, "H1")):
        series = frames.get(tf)
        if not series:
            continue
        if ob.side is Side.SELL:
            series = mirror_all(series)
            lo, hi = -ob.hi, -ob.lo
        else:
            lo, hi = ob.lo, ob.hi
        left_t, conf_t = _scan_buy(series, ob.fu.t, lo, hi, tol, p)
        if left_t is not None and (left_any is None or left_t < left_any):
            left_any = left_t
        if conf_t is not None and (best is None or conf_t < (best.confirm_t or 0)):
            best = ZoneStatus("CONFIRMED", left_t, conf_t, tf)
    return best or ZoneStatus("NEW", left_any)
