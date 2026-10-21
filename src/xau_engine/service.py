"""Оркестратор: свечи -> сопровождение активных сигналов -> анализ -> новые сигналы.

Сервис ничего не знает про Telegram: он возвращает список OutMessage, а слой бота
решает, кому и как их доставить (фильтры пользователя, тихие часы, график).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .config import Settings
from .db import Database
from .engine.analyzer import Analysis, Candidate, analyze
from .models import Candle, Side
from .signals import (
    ENTRY,
    PENDING,
    SKIPPED,
    WATCH,
    Signal,
    from_candidate,
)
from .texts import event_text, signal_text, watch_text
from .timeutil import day_key, now_ts, trading_day_end
from .tracker import (
    TrackEvent,
    apply_cancel,
    apply_entry,
    apply_sl,
    apply_tp,
    apply_zone,
    simulate,
)

log = logging.getLogger("xau_engine.service")

M5 = 300
WATCH_MAX_AGE_S = 48 * 3600  # «на наблюдение» и «пропущено» — только свежие зоны
WATCH_PER_RUN = 1
WATCH_PER_6H = 3
HISTORY_M5 = 5000  # Twelve Data отдаёт до 5000 точек за запрос (~17 дней M5)


@dataclass
class OutMessage:
    kind: str  # SIGNAL | WATCH | EVENT
    signal: Signal
    text: str
    event: str = ""
    chart: bool = False


class Service:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.cfg = settings
        self.p = settings.params
        self._lock = threading.RLock()
        self._analysis: Analysis | None = None
        self._analysis_key: tuple | None = None

    # ------------------------------------------------------------------ данные
    def ingest(self, tf: str, candles: list[Candle]) -> int:
        return self.db.upsert_candles(tf, candles)

    def frames(self) -> tuple[list[Candle], list[Candle], list[Candle]]:
        return (
            self.db.load_candles("M5", HISTORY_M5),
            self.db.load_candles("D1", 400),
            self.db.load_candles("W", 200),
        )

    def last_price(self) -> float | None:
        m5 = self.db.load_candles("M5", 1)
        return m5[-1].c if m5 else None

    def feed_label(self) -> str:
        return self.cfg.feed_label

    # ------------------------------------------------------------------ анализ
    def current_analysis(self, force: bool = False) -> Analysis:
        m5, d1, w = self.frames()
        key = (m5[-1].t if m5 else 0, len(d1), len(w))
        if not force and self._analysis is not None and self._analysis_key == key:
            return self._analysis
        self._analysis = analyze(m5, d1, w, self.p, feed=self.feed_label())
        self._analysis_key = key
        return self._analysis

    # ------------------------------------------------------- шаг по новым свечам
    def step(self) -> list[OutMessage]:
        """Вызывается после поступления новых закрытых M5: сопровождение, затем анализ."""
        with self._lock:
            msgs = self.track_active()
            if self.cfg.mode in ("B", "BOTH"):
                msgs += self.analyze_and_create()
            return msgs

    # ----------------------------------------------------------- сопровождение
    def track_active(self) -> list[OutMessage]:
        msgs: list[OutMessage] = []
        for sig in self.db.active_signals():
            if sig.source == "A" and not self.cfg.track_a_from_bars:
                continue  # сигналы индикатора сопровождают его собственные события
            bars = self.db.load_candles("M5", since=sig.track_from_t)
            if not bars:
                continue
            new, events = simulate(
                sig, bars, self.p, min_rr=self.p.min_rr, max_age_s=self.cfg.max_signal_age_h * 3600
            )
            for ev in events:
                if self.db.add_event(sig.id, ev.kind, ev.t, ev.price, {"reason": ev.reason, "points": ev.points}):
                    msgs.append(self._event_message(new, ev))
            if new.status != sig.status or new.sl != sig.sl or new.tps_hit != sig.tps_hit:
                self.db.save_signal(new)
        return msgs

    def _event_message(self, sig: Signal, ev: TrackEvent) -> OutMessage:
        return OutMessage("EVENT", sig, event_text(sig, ev, self.p), ev.kind, chart=ev.kind == "ENTRY")

    # ------------------------------------------------------------ новые сигналы
    def analyze_and_create(self) -> list[OutMessage]:
        a = self.current_analysis()
        return self.register(a.candidates)

    def register(self, candidates: list[Candidate], source: str = "B") -> list[OutMessage]:
        msgs: list[OutMessage] = []
        watch_now = 0
        for c in candidates:
            existing = self.db.get_by_key(c.key)
            if c.status != "READY" and c.ob.fu.t < c.created_t - WATCH_MAX_AGE_S:
                continue  # старая зона: ни наблюдения, ни записи в истории (иначе завал при старте)
            if c.status == "READY":
                if existing is not None and existing.kind == "SIGNAL":
                    continue
                if not self._allowed_now(c.side, c.zone.lo, c.zone.hi, c.created_t):
                    continue
                sig = from_candidate(c, "SIGNAL", source)
                self._apply_ttl(sig)
                if existing is not None:  # наблюдение превратилось в сигнал
                    sig.id = existing.id
                    self.db.save_signal(sig)
                else:
                    self.db.insert_signal(sig)
                msgs.append(OutMessage("SIGNAL", sig, signal_text(sig, self.p), chart=True))
                if sig.status == ENTRY:  # точка 3 уже есть: событие фиксируем без повторного сообщения
                    self.db.add_event(sig.id, "ZONE", sig.entered_zone_t or sig.entry_t, None)
                    self.db.add_event(sig.id, "ENTRY", sig.entry_t, None)
            elif c.status == "WATCH":
                if existing is not None:
                    continue
                if watch_now >= WATCH_PER_RUN or self.db.count_watch_since(c.created_t - 6 * 3600) >= WATCH_PER_6H:
                    continue
                watch_now += 1
                sig = from_candidate(c, "WATCH", source)
                self.db.insert_signal(sig)
                msgs.append(OutMessage("WATCH", sig, watch_text(sig, self.p)))
            else:  # SKIPPED: фиксируем один раз, видно в истории
                if existing is None:
                    self.db.insert_signal(from_candidate(c, "SKIPPED", source))
                elif existing.kind == "WATCH":
                    sk = from_candidate(c, "SKIPPED", source)
                    sk.id = existing.id
                    self.db.save_signal(sk)
        return msgs

    def _apply_ttl(self, sig: Signal) -> None:
        if self.cfg.signal_ttl_hours:
            sig.expires_t = int(sig.created_t + self.cfg.signal_ttl_hours * 3600)
        else:
            sig.expires_t = trading_day_end(sig.created_t)

    def _allowed_now(self, side: Side, lo: float, hi: float, ts: int) -> bool:
        """Лимит сигналов в день и защита от дублей по пересекающейся зоне."""
        if self.db.count_signals_day(day_key(ts)) >= self.cfg.max_signals_day:
            log.info("лимит сигналов за день достигнут (%s)", self.cfg.max_signals_day)
            return False
        pad = self.p.px(self.p.zone_max)
        for s in self.db.active_signals():
            if s.side is side and s.zone_lo - pad <= hi and lo - pad <= s.zone_hi:
                return False
        return True

    # ------------------------------------------------------ вариант A: вебхуки
    def handle_webhook(self, payload: Mapping[str, Any]) -> list[OutMessage]:
        """Событие от индикатора TradingView (тип: signal | watch | event | bar)."""
        kind = str(payload.get("type", "")).lower()
        with self._lock:
            if kind == "bar":
                return self._webhook_bar(payload)
            if kind in ("signal", "watch"):
                return self._webhook_signal(payload, kind)
            if kind == "event":
                return self._webhook_event(payload)
        raise ValueError(f"неизвестный тип алерта: {kind!r}")

    def _webhook_bar(self, d: Mapping[str, Any]) -> list[OutMessage]:
        t = parse_time(d["time"])
        bar = Candle(t - t % M5, float(d["open"]), float(d["high"]), float(d["low"]), float(d["close"]))
        self.db.upsert_candles("M5", [bar])
        return self.track_active()

    def _webhook_signal(self, d: Mapping[str, Any], kind: str) -> list[OutMessage]:
        side = Side(str(d["side"]).upper())
        lo, hi = sorted((float(d["zone_lo"]), float(d["zone_hi"])))
        created = parse_time(d.get("time")) if d.get("time") else now_ts()
        key = str(d.get("id") or f"A:{side.value}:{lo:.1f}:{hi:.1f}:{day_key(created)}")
        if self.db.get_by_key(key) is not None:
            return []
        tps = [float(x) for x in d.get("tps", []) if x not in (None, "")]
        sl = float(d.get("sl", lo if side is Side.BUY else hi))
        sig = Signal(
            key=key, side=side, kind="WATCH" if kind == "watch" else "SIGNAL",
            status=WATCH if kind == "watch" else PENDING,
            strength=str(d.get("strength", "C")).upper(), zone_lo=lo, zone_hi=hi,
            true_stop=float(d.get("true_stop", lo if side is Side.BUY else hi)), sl=sl, tps=tps,
            tp_labels=list(d.get("tp_labels", [])),
            confirmations=[(bool(x[0]), str(x[1])) for x in d.get("confirmations", [])],
            tfs=list(d.get("tfs", [])), feed=str(d.get("feed", self.cfg.feed_label_a)),
            created_t=created, expires_t=int(d["expires"]) if d.get("expires") else trading_day_end(created),
            track_from_t=created, level4=float(d["level4"]) if d.get("level4") else None,
            ob_tf=str(d.get("ob_tf", "")), ob_label=str(d.get("ob_label", "")),
            best_sign=str(d.get("sign", "")), source="A", sl_initial=sl,
            reason=str(d.get("reason", "")),
        )
        if not d.get("expires"):
            self._apply_ttl(sig)
        if kind == "watch":
            self.db.insert_signal(sig)
            return [OutMessage("WATCH", sig, watch_text(sig, self.p))]
        reason = self._validate_webhook_signal(sig)
        if reason:
            sig.kind, sig.status, sig.reason = "SKIPPED", SKIPPED, reason
            self.db.insert_signal(sig)
            log.info("сигнал A отклонён: %s", reason)
            return []
        if not self._allowed_now(sig.side, sig.zone_lo, sig.zone_hi, sig.created_t):
            return []
        self.db.insert_signal(sig)
        return [OutMessage("SIGNAL", sig, signal_text(sig, self.p), chart=True)]

    def _validate_webhook_signal(self, sig: Signal) -> str:
        """Сервер перепроверяет то, что прислал индикатор (раздел 3, шаги 4 и 7; раздел 4)."""
        p = self.p
        if sig.strength not in ("A+", "A", "B"):
            return "class_c"
        if sig.width_pts(p) > p.zone_max + 1e-9:
            return "wide"  # пропущено: широкая зона
        if not sig.tps:
            return "no_target"
        if sig.risk <= 0 or p.pts(sig.risk) > p.sl_max:
            return "sl_far"
        if p.min_rr is not None and sig.rr(0) < p.min_rr:
            return "rr"
        if (sig.sl >= sig.zone_lo) if sig.side is Side.BUY else (sig.sl <= sig.zone_hi):
            return "bad_sl"
        return ""

    def _webhook_event(self, d: Mapping[str, Any]) -> list[OutMessage]:
        sig = self.db.get_by_key(str(d["id"]))
        if sig is None or not sig.active:
            return []
        name = str(d["event"]).upper()
        t = parse_time(d.get("time")) if d.get("time") else now_ts()
        price = float(d["price"]) if d.get("price") not in (None, "") else sig.mid
        p = self.p
        ev: TrackEvent | None
        if name == "ZONE":
            ev = apply_zone(sig, t)
        elif name == "ENTRY":
            tip = float(d["p2_tip"]) if d.get("p2_tip") not in (None, "") else None
            ev = apply_entry(sig, t, price, p, p.min_rr, tip, bool(d.get("direct")))
        elif name in ("TP1", "TP2", "TP3"):
            ev = apply_tp(sig, int(name[2]), t, p) if sig.entered else None
        elif name == "SL":
            ev = apply_sl(sig, t, p) if sig.entered else apply_cancel(sig, t, "цена пробила уровень стопа до входа", p)
        elif name in ("CANCEL", "EXPIRED"):
            ev = apply_cancel(sig, t, str(d.get("reason", "отменён индикатором")), p)
        else:
            raise ValueError(f"неизвестное событие: {name!r}")
        if ev is None:
            return []
        if not self.db.add_event(sig.id, ev.kind, ev.t, ev.price, {"reason": ev.reason, "points": ev.points}):
            return []
        self.db.save_signal(sig)
        return [self._event_message(sig, ev)]


def parse_time(v: Any) -> int:
    """Секунды/миллисекунды Unix либо ISO-строка (формат {{time}} TradingView)."""
    if isinstance(v, (int, float)):
        x = int(v)
        return x // 1000 if x > 10**11 else x
    s = str(v).strip()
    if s.isdigit():
        return parse_time(int(s))
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
