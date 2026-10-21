"""Время: всё хранится в UTC, пользователю показывается Бишкек (UTC+6, без перехода)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

BISHKEK = timezone(timedelta(hours=6), "Бишкек")
TTL_END_HOUR = 23  # сигнал действует до 23:00 Бишкек (конец торгового дня)


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def to_bishkek(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, BISHKEK)


def fmt_dt(ts: int) -> str:
    return to_bishkek(ts).strftime("%d.%m %H:%M")


def fmt_hm(ts: int) -> str:
    return to_bishkek(ts).strftime("%H:%M")


def day_key(ts: int) -> str:
    """Календарный день по Бишкеку (для лимита сигналов и итогов за день)."""
    return to_bishkek(ts).strftime("%Y-%m-%d")


def week_key(ts: int) -> str:
    y, w, _ = to_bishkek(ts).isocalendar()
    return f"{y}-W{w:02d}"


def trading_day_end(ts: int) -> int:
    """Ближайшие 23:00 по Бишкеку, не раньше чем через 30 минут."""
    local = to_bishkek(ts)
    end = local.replace(hour=TTL_END_HOUR, minute=0, second=0, microsecond=0)
    if end - local < timedelta(minutes=30):
        end += timedelta(days=1)
    return int(end.timestamp())


def in_quiet_hours(ts: int, start_h: int, end_h: int) -> bool:
    """Тихие часы [start_h, end_h) по Бишкеку; допускает переход через полночь."""
    if start_h == end_h:
        return False
    h = to_bishkek(ts).hour
    if start_h < end_h:
        return start_h <= h < end_h
    return h >= start_h or h < end_h
