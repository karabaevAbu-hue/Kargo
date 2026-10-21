"""Сопровождение сигнала: цена вошла в зону, точка 3, TP1-TP3, SL, отмена.

Симуляция идёт по закрытым свечам M5 с момента создания сигнала. Результат
детерминирован: тот же набор свечей всегда даёт тот же набор событий, поэтому
сервис просто пересчитывает сигнал и рассылает только новые события.
Если в одной свече задеты и SL, и TP, считаем, что первым задет SL (консервативно).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from .engine.cycle import CycleMachine, is_direct_entry
from .engine.params import Params
from .models import Candle, Side
from .signals import (
    CANCELLED,
    ENTRY,
    EXPIRED,
    IN_ZONE,
    PENDING,
    SL,
    TP1,
    TP2,
    TP3,
    Signal,
    result_points,
)

M5 = 300


@dataclass(frozen=True)
class TrackEvent:
    kind: str  # ZONE | ENTRY | TP1 | TP2 | TP3 | SL | CANCEL | EXPIRED
    t: int
    price: float
    reason: str = ""
    points: float | None = None


def _close(sig: Signal, status: str, t: int, p: Params) -> None:
    sig.status = status
    sig.closed_t = t
    if sig.entered or sig.tps_hit:
        sig.result = f"TP{sig.tps_hit}" if sig.tps_hit else ("SL" if status == SL else status)
        sig.result_points = result_points(sig, p)
    else:
        sig.result = status
        sig.result_points = None


def apply_zone(sig: Signal, t: int) -> TrackEvent | None:
    if sig.status != PENDING:
        return None
    sig.status = IN_ZONE
    sig.entered_zone_t = t
    return TrackEvent("ZONE", t, sig.mid)


def apply_entry(
    sig: Signal, t: int, price: float, p: Params, min_rr: float | None, p2_tip: float | None = None,
    direct: bool = False,
) -> TrackEvent:
    """Условие входа выполнено. Стоп может углубиться до самого глубокого выноса (точка 2)."""
    if p2_tip is not None:
        buf = p.px(p.sl_buffer)
        new_sl = min(sig.sl, p2_tip - buf) if sig.side is Side.BUY else max(sig.sl, p2_tip + buf)
        if new_sl != sig.sl:
            risk = abs(sig.mid - new_sl)
            if risk > p.px(p.sl_max):
                _close(sig, CANCELLED, t, p)
                return TrackEvent("CANCEL", t, price, "вынос слишком глубокий: стоп дальше SL_MAX")
            if min_rr is not None and sig.tps and abs(sig.tps[0] - sig.mid) / risk < min_rr:
                _close(sig, CANCELLED, t, p)
                return TrackEvent("CANCEL", t, price, "после выноса RR до TP1 ниже минимума")
            sig.sl = new_sl
    sig.status = ENTRY
    sig.entry_t = t
    sig.entry_direct = direct
    if sig.entered_zone_t is None:
        sig.entered_zone_t = t
    return TrackEvent("ENTRY", t, price)


def apply_cancel(sig: Signal, t: int, reason: str, p: Params, status: str = CANCELLED) -> TrackEvent:
    _close(sig, status, t, p)
    return TrackEvent("EXPIRED" if status == EXPIRED else "CANCEL", t, sig.mid, reason)


def apply_tp(sig: Signal, n: int, t: int, p: Params) -> TrackEvent:
    sig.tps_hit = max(sig.tps_hit, n)
    price = sig.tps[n - 1]
    pts = p.pts(abs(price - sig.mid))
    if n >= len(sig.tps):
        _close(sig, TP3, t, p)
    else:
        sig.status = TP1 if n == 1 else TP2
    return TrackEvent(f"TP{n}", t, price, points=pts)


def apply_sl(sig: Signal, t: int, p: Params) -> TrackEvent:
    _close(sig, SL, t, p)
    return TrackEvent("SL", t, sig.sl, points=sig.result_points)


def _hit_against(side: Side, k: Candle):
    return (lambda lv: k.l <= lv) if side is Side.BUY else (lambda lv: k.h >= lv)


def _hit_with(side: Side, k: Candle):
    return (lambda lv: k.h >= lv) if side is Side.BUY else (lambda lv: k.l <= lv)


def simulate(
    sig: Signal,
    bars: Sequence[Candle],
    p: Params,
    min_rr: float | None = None,
    max_age_s: float = 36 * 3600,
) -> tuple[Signal, list[TrackEvent]]:
    """Проигрывает свечи M5 от создания сигнала. Возвращает обновлённую копию и события."""
    s = replace(sig, tps=list(sig.tps), confirmations=list(sig.confirmations))
    # состояние восстанавливаем с начала; вход, случившийся ещё до отправки, сохраняем
    born_entered = sig.entry_t is not None and sig.entry_t <= sig.track_from_t
    s.status = ENTRY if born_entered else PENDING
    if not born_entered:
        s.entered_zone_t = s.entry_t = None
    s.closed_t = None
    s.tps_hit, s.result, s.result_points = 0, "", None
    s.sl = s.sl_initial if s.sl_initial is not None else s.sl
    events: list[TrackEvent] = []
    side = s.side
    machine = CycleMachine(side, s.zone_lo, s.zone_hi)
    hist: list[Candle] = []

    for k in bars:
        if k.t < s.track_from_t or s.status in (SL, TP3, CANCELLED, EXPIRED):
            continue
        te = k.t + M5
        hist.append(k)
        low_hit = _hit_against(side, k)  # задет ли уровень против сделки (SL)
        high_hit = _hit_with(side, k)  # задет ли уровень по ходу сделки (TP)

        if s.status in (PENDING, IN_ZONE):
            if te > s.expires_t:
                events.append(apply_cancel(s, s.expires_t, "истёк срок", p, EXPIRED))
                continue
            if low_hit(s.sl):
                events.append(apply_cancel(s, te, "цена пробила уровень стопа до входа", p))
                continue
            if s.tps and high_hit(s.tps[0]):
                events.append(apply_cancel(s, te, "цена ушла к цели без входа", p))
                continue
            if te % 3600 == 0 and ((k.c < s.zone_lo) if side is Side.BUY else (k.c > s.zone_hi)):
                events.append(apply_cancel(s, te, "зона снята: закрытие H1 за зоной", p))
                continue
            if s.level4 is not None and ((k.c < s.level4) if side is Side.BUY else (k.c > s.level4)):
                events.append(apply_cancel(s, te, "пробит Level #4", p))
                continue
            cy = machine.feed(k)
            if cy.stage >= 1:
                ev = apply_zone(s, k.t)
                if ev:
                    events.append(ev)
            direct = None
            if not cy.entered and len(hist) >= 3 and is_direct_entry(
                hist[-3], hist[-2], hist[-1], side, s.zone_lo, s.zone_hi, p
            ):
                b = hist[-2]
                direct = (b.l if side is Side.BUY else b.h)
                if s.status == PENDING:
                    events.append(apply_zone(s, b.t) or TrackEvent("ZONE", b.t, s.mid))
            if cy.entered or direct is not None:
                if cy.entered and not cy.in_zone:
                    events.append(apply_cancel(s, te, "вход упущен: закрытие точки 3 вне зоны", p))
                    continue
                tip = cy.p2_tip if cy.entered else direct
                events.append(apply_entry(s, te, k.c, p, min_rr, tip, direct=not cy.entered))
            continue

        # сделка открыта: SL, затем TP по порядку
        if s.entry_t is not None and te - s.entry_t > max_age_s:
            events.append(apply_cancel(s, te, "истёк срок после входа", p, EXPIRED))
            continue
        if low_hit(s.sl):
            events.append(apply_sl(s, te, p))
            continue
        while s.status not in (TP3,) and s.tps_hit < len(s.tps) and high_hit(s.tps[s.tps_hit]):
            events.append(apply_tp(s, s.tps_hit + 1, te, p))
    return s, events
