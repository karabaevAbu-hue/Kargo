"""Manipulation Cycle (M30/M15/M5): ждать точки 1 и 2, входить в точке 3.

Бычья модель: 1 — первая реакция у Prev LOW (цена вошла в зону),
2 — вынос ниже Prev LOW в SL Cluster (Stop Hunt), 3 — закрытие обратно выше Prev LOW.
Медвежья модель зеркальна (Prev HIGH).
Прямой вход: манипуляция «тело внутри тени» (BIW), за которой сразу идёт Doji.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..models import Candle, Side
from .params import Params
from .patterns import detect_fu, inside_body, is_small_body


@dataclass
class Cycle:
    stage: int = 0  # 0 — ничего, 1 — точка 1, 2 — точка 2, 3 — точка 3 (вход)
    p1_t: int | None = None
    p2_t: int | None = None
    p2_tip: float | None = None  # самый глубокий вынос
    p3_t: int | None = None
    p3_close: float | None = None
    in_zone: bool = False  # закрытие точки 3 внутри зоны
    direct: bool = False  # вход прямой (BIW + Doji)

    @property
    def entered(self) -> bool:
        return self.stage >= 3


class CycleMachine:
    """Пошаговый автомат цикла: кормится закрытыми свечами M5 по одной."""

    def __init__(self, side: Side, zone_lo: float, zone_hi: float):
        self.side = side
        self.sign = side.sign
        if side is Side.SELL:  # зеркало: работаем в бычьей форме
            self.zl, self.zh = -zone_hi, -zone_lo
        else:
            self.zl, self.zh = zone_lo, zone_hi
        self.cycle = Cycle()
        self._n = 0
        self._p1_idx = -1

    def feed(self, candle: Candle) -> Cycle:
        cy = self.cycle
        if cy.stage >= 3:
            return cy
        k = candle.mirrored() if self.side is Side.SELL else candle
        i = self._n
        self._n += 1
        if cy.stage == 0:
            if k.l > self.zh:
                return cy
            cy.stage, self._p1_idx, cy.p1_t = 1, i, candle.t
        if k.l < self.zl:
            tip = candle.l if self.side is Side.BUY else candle.h
            if cy.stage == 1:
                cy.stage, cy.p2_t, cy.p2_tip = 2, candle.t, tip
            else:
                prev = cy.p2_tip if cy.p2_tip is not None else tip
                cy.p2_tip = min(prev, tip) if self.side is Side.BUY else max(prev, tip)
        if cy.stage == 2 and k.c > self.zl and i > self._p1_idx:
            cy.stage, cy.p3_t, cy.p3_close = 3, candle.t, candle.c
            cy.in_zone = k.c <= self.zh
        return cy


def detect_cycle(
    candles: Sequence[Candle], side: Side, zone_lo: float, zone_hi: float, after_t: int = 0
) -> Cycle:
    """Идёт по закрытым свечам (M5) после after_t и ищет точки 1-3."""
    m = CycleMachine(side, zone_lo, zone_hi)
    for k in candles:
        if k.t < after_t:
            continue
        if m.feed(k).entered:
            break
    return m.cycle


def is_direct_entry(
    a: Candle, b: Candle, d: Candle, side: Side, zone_lo: float, zone_hi: float, p: Params
) -> bool:
    """a, b, d — три подряд закрытые свечи: b — BIW-манипуляция у зоны, d — Doji внутри зоны."""
    fu = detect_fu([a, b], 1, side, p)
    if not fu or not fu.biw:
        return False
    if not ((b.l <= zone_hi) if side is Side.BUY else (b.h >= zone_lo)):
        return False
    if not (is_small_body(d, p.doji_body_ratio) or inside_body(d, b)):
        return False
    return zone_lo <= d.c <= zone_hi


def detect_direct_entry(
    candles: Sequence[Candle], side: Side, zone_lo: float, zone_hi: float, p: Params, after_t: int = 0
) -> Cycle | None:
    """BIW-манипуляция у зоны и сразу за ней Doji. Возвращает Cycle со stage=3 и direct=True."""
    for i in range(2, len(candles)):
        d = candles[i]
        if d.t < after_t:
            continue
        if is_direct_entry(candles[i - 2], candles[i - 1], d, side, zone_lo, zone_hi, p):
            b = candles[i - 1]
            tip = b.l if side is Side.BUY else b.h
            return Cycle(3, b.t, b.t, tip, d.t, d.c, True, True)
    return None
