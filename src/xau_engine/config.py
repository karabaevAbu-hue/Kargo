"""Настройки из переменных окружения / файла .env (раздел 6 ТЗ).

Все числа, которых нет в слайдах Mr. Casino, меняются здесь без правки кода.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .engine.params import Params


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _float(name: str, default: float) -> float:
    v = _env(name)
    return float(v) if v is not None else default


def _int(name: str, default: int) -> int:
    v = _env(name)
    return int(v) if v is not None else default


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on", "да")


@dataclass(frozen=True)
class Settings:
    # доступ и инфраструктура
    bot_token: str = ""
    allowed_user_ids: frozenset[int] = frozenset()
    webhook_secret: str = ""
    mode: str = "B"  # B — свой расчёт (Twelve Data, основной сейчас), A — вебхуки TradingView (платный тариф), BOTH
    db_path: str = "data/xau_engine.sqlite3"
    host: str = "0.0.0.0"
    port: int = 8080
    dry_run: bool = False  # печатать сообщения в лог вместо отправки в Telegram

    # фид
    twelvedata_api_key: str = ""
    td_symbol: str = "XAU/USD"
    feed_label_a: str = "OANDA"
    feed_label_b: str = "Twelve Data"
    tv_symbol: str = "OANDA:XAUUSD"

    # поведение
    max_signals_day: int = 3
    signal_ttl_hours: float | None = None  # None — до конца торгового дня (23:00 Бишкек)
    max_signal_age_h: float = 36.0  # предел жизни сделки после входа
    quiet_start: int = 0
    quiet_end: int = 8
    send_watch: bool = True
    analysis_cooldown_s: int = 300
    track_a_from_bars: bool = False  # сопровождать сигналы индикатора ещё и по свечам M5

    params: Params = field(default_factory=Params)

    @property
    def feed_label(self) -> str:
        return self.feed_label_b if self.mode == "B" else self.feed_label_a

    @property
    def tradingview_url(self) -> str:
        return "https://www.tradingview.com/chart/?symbol=" + self.tv_symbol.replace(":", "%3A")


def load_settings(dotenv: str | Path | None = ".env") -> Settings:
    if dotenv:
        _load_dotenv(Path(dotenv))
    mode = (_env("MODE", "B") or "B").upper()
    if mode not in ("A", "B", "BOTH"):
        raise ValueError("MODE должен быть A, B или BOTH")
    rr_raw = (_env("MIN_RR", "2") or "2").strip().lower()
    min_rr = None if rr_raw in ("off", "none", "0", "false") else float(rr_raw)
    params = Params(
        point_size=_float("POINT_SIZE", 1.0),
        zone_target=_float("ZONE_TARGET", 5.0),
        zone_max=_float("ZONE_MAX", 10.0),
        sl_buffer=_float("SL_BUFFER", 1.5),
        sl_max=_float("SL_MAX", 20.0),
        min_rr=min_rr,
        max_zone_distance=_float("MAX_ZONE_DISTANCE", 60.0),
        doji_body_ratio=_float("DOJI_BODY_RATIO", 0.10),
        big_wick_ratio=_float("BIG_WICK_RATIO", 0.50),
        perfect_double_tol=_float("PERFECT_DOUBLE_TOL", 0.5),
        big_imbalance=_float("BIG_IMBALANCE", 3.0),
        min_zone_width=_float("MIN_ZONE_WIDTH", 1.0),
        strong_body_ratio=_float("STRONG_BODY_RATIO", 0.40),
        ob_max_wick_ratio=_float("OB_MAX_WICK_RATIO", 0.25),
        displacement_atr=_float("DISPLACEMENT_ATR", 1.0),
        trendline_tol=_float("TRENDLINE_TOL", 0.5),
        # OANDA открывает день около 21:00 UTC; Twelve Data (вариант B) режет сутки по UTC
        day_open_utc_hour=_int("DAY_OPEN_UTC_HOUR", 0 if mode == "B" else 21),
    )
    allowed = frozenset(
        int(x) for x in (_env("ALLOWED_USER_IDS", "") or "").replace(";", ",").split(",") if x.strip()
    )
    ttl = _env("SIGNAL_TTL_HOURS")
    return Settings(
        bot_token=_env("BOT_TOKEN", "") or "",
        allowed_user_ids=allowed,
        webhook_secret=_env("WEBHOOK_SECRET", "") or "",
        mode=mode,
        db_path=_env("DB_PATH", "data/xau_engine.sqlite3") or "data/xau_engine.sqlite3",
        host=_env("HOST", "0.0.0.0") or "0.0.0.0",
        port=_int("PORT", 8080),
        dry_run=_bool("DRY_RUN", False),
        twelvedata_api_key=_env("TWELVEDATA_API_KEY", "") or "",
        td_symbol=_env("TD_SYMBOL", "XAU/USD") or "XAU/USD",
        feed_label_a=_env("FEED_LABEL_A", "OANDA") or "OANDA",
        feed_label_b=_env("FEED_LABEL_B", "Twelve Data") or "Twelve Data",
        tv_symbol=_env("TV_SYMBOL", "OANDA:XAUUSD") or "OANDA:XAUUSD",
        max_signals_day=_int("MAX_SIGNALS_DAY", 3),
        signal_ttl_hours=float(ttl) if ttl else None,
        max_signal_age_h=_float("MAX_SIGNAL_AGE_H", 36.0),
        quiet_start=_int("QUIET_START", 0),
        quiet_end=_int("QUIET_END", 8),
        send_watch=_bool("SEND_WATCH", True),
        analysis_cooldown_s=_int("ANALYSIS_COOLDOWN_S", 300),
        track_a_from_bars=_bool("TRACK_A_FROM_BARS", False),
        params=params,
    )
