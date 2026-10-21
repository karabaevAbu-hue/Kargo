"""Параметры движка. Всё, чего нет в слайдах стратегии, вынесено сюда (раздел 6 ТЗ)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Params:
    # Пункт: 1 пункт = point_size долларов цены золота (открытый вопрос 1).
    point_size: float = 1.0
    zone_target: float = 5.0  # пунктов
    zone_max: float = 10.0
    min_zone_width: float = 1.0
    sl_buffer: float = 1.5
    sl_max: float = 20.0
    min_rr: float | None = 2.0  # RR до TP1; None — проверка выключена
    max_zone_distance: float = 60.0  # пунктов от цены до зоны (защитная настройка)

    # Свечные пороги (в слайдах не заданы).
    doji_body_ratio: float = 0.10
    big_wick_ratio: float = 0.50
    perfect_double_tol: float = 0.5  # пунктов
    big_imbalance: float = 3.0  # пунктов (для M5)
    strong_body_ratio: float = 0.40  # SFU/BIW: доля тела в диапазоне FU-свечи
    ob_max_wick_ratio: float = 0.25  # odd-свеча: суммарная доля теней
    displacement_atr: float = 1.0  # импульс после OB в ATR
    trendline_tol: float = 0.5  # пунктов

    # Сетка времени (дневная свеча OANDA открывается около 17:00 Нью-Йорк).
    day_open_utc_hour: int = 21

    def px(self, points: float) -> float:
        """Пункты -> цена."""
        return points * self.point_size

    def pts(self, price: float) -> float:
        """Цена -> пункты."""
        return price / self.point_size
