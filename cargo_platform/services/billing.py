"""Расчёт стоимости (ТЗ, раздел 6.1). Чистые функции, без базы.

Сумма = max(вес итого, мин. вес) × цена за кг + хранение − скидка
Сумма считается на весь приход клиента в партии, а не на каждую посылку.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

TENTH = Decimal("0.1")


def parse_weight(raw) -> Decimal | None:
    """'1,25' / '1.25 кг' / 1.25 -> Decimal. Пусто, ноль и мусор -> None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        value = Decimal(str(raw))
    else:
        text = re.sub(r"(?i)\s*(кг|kg|к)\.?\s*$", "", str(raw).strip()).replace(",", ".").replace(" ", "")
        try:
            value = Decimal(text)
        except Exception:
            return None
    if value <= 0 or value > 100000:
        return None
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ceil_tenth(value: Decimal) -> Decimal:
    """Округление ВВЕРХ до 0,1 кг."""
    return (Decimal(math.ceil(value * 10)) / 10).quantize(TENTH)


def volume_weight(length: Decimal, width: Decimal, height: Decimal, divisor: int) -> Decimal:
    return (length * width * height / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def effective_weight(weight: Decimal | None, vol: Decimal | None, divisor: int | None) -> Decimal | None:
    """Если включён объёмный вес: берём большее из фактического и объёмного."""
    if weight is None:
        return None
    if divisor and vol is not None and vol > weight:
        return vol
    return weight


def calc_amount(weights: list[Decimal], price: Decimal, min_weight: Decimal | None) -> tuple[Decimal, int]:
    """(вес итого, сумма за вес в целых сомах). Хранение и скидка добавляются в charge_total."""
    total = ceil_tenth(sum(weights, Decimal(0)))
    billable = max(total, min_weight or Decimal(0))
    amount = int((billable * Decimal(price)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return total, amount


def charge_total(amount: int, storage: int, discount: int) -> int:
    return max(0, amount + storage - discount)


def storage_amount(arrived_at: datetime | None, now: datetime, free_days: int | None, price_day) -> int:
    """Хранение: после N бесплатных дней — X сом в день. Нет настроек — 0."""
    if arrived_at is None or free_days is None or not price_day:
        return 0
    days_over = (now - arrived_at).days - free_days
    return int(Decimal(price_day) * days_over) if days_over > 0 else 0


# ---------- форматирование для сообщений ----------

def fmt_money(n) -> str:
    return f"{int(n):,}".replace(",", " ") + " сом"


def fmt_weight(w) -> str:
    if w is None:
        return "—"
    w = Decimal(w)
    digits = 1 if w == w.quantize(TENTH) else 2
    return f"{w:.{digits}f}".replace(".", ",") + " кг"


def fmt_price(p) -> str:
    """280.00 -> '280', 280.50 -> '280.5' (без хвоста нулей)."""
    return format(Decimal(p).normalize(), "f")
