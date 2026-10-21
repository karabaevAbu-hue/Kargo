"""Базовые типы: свеча и направление."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


@dataclass(frozen=True, slots=True)
class Candle:
    """Закрытая свеча. t — время открытия, секунды UTC."""

    t: int
    o: float
    h: float
    l: float
    c: float

    @property
    def body_hi(self) -> float:
        return self.o if self.o > self.c else self.c

    @property
    def body_lo(self) -> float:
        return self.o if self.o < self.c else self.c

    @property
    def body(self) -> float:
        return self.body_hi - self.body_lo

    @property
    def range(self) -> float:
        return self.h - self.l

    @property
    def upper_wick(self) -> float:
        return self.h - self.body_hi

    @property
    def lower_wick(self) -> float:
        return self.body_lo - self.l

    @property
    def bull(self) -> bool:
        return self.c > self.o

    @property
    def bear(self) -> bool:
        return self.c < self.o

    def mirrored(self) -> "Candle":
        """Зеркало цены (p -> -p): бычья схема превращается в медвежью."""
        return Candle(self.t, -self.o, -self.l, -self.h, -self.c)


def mirror_all(candles) -> list[Candle]:
    return [c.mirrored() for c in candles]
