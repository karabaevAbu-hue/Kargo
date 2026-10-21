"""Проверка на истории (этап 5 ТЗ): сколько сигналов, доля TP1, средний RR, ни одной зоны шире ZONE_MAX.

Историю прогоняем через тот же Service и тот же трекер, что работают вживую,
свеча за свечой, без заглядывания вперёд.
"""
from __future__ import annotations

import csv
import dataclasses
import json
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .config import Settings
from .db import Database
from .engine.analyzer import SKIP_TEXT
from .engine.candles import resample
from .models import Candle
from .service import Service
from .timeutil import day_key

M5 = 300


# ------------------------------------------------------------------ загрузка данных
def parse_ts(v: str) -> int:
    v = v.strip()
    if v.replace(".", "", 1).isdigit():
        x = int(float(v))
        return x // 1000 if x > 10**11 else x
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def load_csv(path: str | Path) -> list[Candle]:
    """CSV со столбцами time|datetime|timestamp, open, high, low, close (время в UTC)."""
    rows: list[Candle] = []
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        cols = {(c or "").strip().lower(): c for c in (rd.fieldnames or [])}
        tcol = next((cols[k] for k in ("time", "datetime", "timestamp", "date") if k in cols), None)
        if tcol is None or not all(k in cols for k in ("open", "high", "low", "close")):
            raise ValueError("нужны столбцы time, open, high, low, close")
        for r in rd:
            rows.append(Candle(parse_ts(r[tcol]), float(r[cols["open"]]), float(r[cols["high"]]),
                               float(r[cols["low"]]), float(r[cols["close"]])))
    rows.sort(key=lambda c: c.t)
    return rows


def save_csv(path: str | Path, candles: Sequence[Candle]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "open", "high", "low", "close"])
        for c in candles:
            w.writerow([c.t, f"{c.o:.2f}", f"{c.h:.2f}", f"{c.l:.2f}", f"{c.c:.2f}"])


def synthetic_m5(days: int, seed: int = 1, start: float = 2000.0, vol: float = 0.9) -> list[Candle]:
    """Случайное блуждание для проверки, что конвейер работает. На нём НЕЛЬЗЯ оценивать стратегию."""
    r = random.Random(seed)
    t0 = (1_700_000_000 // 86400) * 86400
    px, out, trend = start, [], 0.0
    for i in range(days * 288):
        if i % 400 == 0:
            trend = r.uniform(-0.12, 0.12)
        o = px
        c = o + trend + r.gauss(0, vol)
        h = max(o, c) + abs(r.gauss(0, vol * 0.5))
        lo = min(o, c) - abs(r.gauss(0, vol * 0.5))
        out.append(Candle(t0 + i * M5, o, h, lo, c))
        px = c
    return out


def derive_htf(m5: Sequence[Candle], day_open_utc_hour: int) -> tuple[list[Candle], list[Candle]]:
    """D1 и W из M5, если отдельных файлов нет (приближение: границы по якорю дня)."""
    return resample(m5, 1440, day_open_utc_hour), resample(m5, 10080, day_open_utc_hour)


# ------------------------------------------------------------------ прогон
@dataclass
class Report:
    bars: int = 0
    days: float = 0.0
    signals: int = 0
    entered: int = 0
    cancelled_before_entry: int = 0
    open_at_end: int = 0
    tp1_or_better: int = 0
    tp2_or_better: int = 0
    tp3: int = 0
    sl: int = 0
    points: float = 0.0
    avg_rr1: float = 0.0
    max_zone_width: float = 0.0
    zones_over_max: int = 0
    max_per_day: int = 0
    strengths: dict = field(default_factory=dict)
    skipped: dict = field(default_factory=dict)
    cancel_reasons: dict = field(default_factory=dict)
    sides: dict = field(default_factory=dict)

    @property
    def tp1_rate(self) -> float:
        return self.tp1_or_better / self.entered if self.entered else 0.0

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["tp1_rate"] = self.tp1_rate
        return d


def run_backtest(
    m5: Sequence[Candle],
    d1: Sequence[Candle],
    w: Sequence[Candle],
    cfg: Settings,
    warmup: int = 600,
    stride: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> Report:
    cfg = dataclasses.replace(cfg, mode="B")
    db = Database(":memory:")
    svc = Service(db, cfg)
    svc.ingest("D1", list(d1))
    svc.ingest("W", list(w))
    p = cfg.params
    for i, bar in enumerate(m5):
        db.upsert_candles("M5", [bar])
        if i < warmup:
            continue
        if db.active_signals():
            svc.track_active()
        if (i - warmup) % stride == 0:
            svc.analyze_and_create()
        if progress and i % 2000 == 0:
            progress(i, len(m5))
    svc.track_active()

    rep = Report(bars=len(m5), days=len(m5) / 288)
    rows = db._signals("kind IN ('SIGNAL','SKIPPED')", (), "created_t")
    per_day: Counter = Counter()
    rr_list: list[float] = []
    skipped: Counter = Counter()
    cancels: Counter = Counter()
    strengths: Counter = Counter()
    sides: Counter = Counter()
    for s in rows:
        if s.kind == "SKIPPED":
            skipped[SKIP_TEXT.get(s.reason, s.reason)] += 1
            continue
        rep.signals += 1
        per_day[day_key(s.created_t)] += 1
        strengths[s.strength] += 1
        sides[s.side.value] += 1
        width = s.width_pts(p)
        rep.max_zone_width = max(rep.max_zone_width, width)
        if width > p.zone_max + 1e-9:
            rep.zones_over_max += 1
        if s.tps:
            rr_list.append(s.rr(0))
        if s.entry_t is not None:
            rep.entered += 1
        if s.active:
            rep.open_at_end += 1
        if s.status == "CANCELLED" or s.status == "EXPIRED":
            if s.entry_t is None:
                rep.cancelled_before_entry += 1
            events = db.events(s.id)
            reason = next((json.loads(e["payload"]).get("reason") for e in events if e["kind"] in ("CANCEL", "EXPIRED")), "")
            cancels[reason or s.status] += 1
        if s.tps_hit >= 1:
            rep.tp1_or_better += 1
        if s.tps_hit >= 2:
            rep.tp2_or_better += 1
        if s.status == "TP3":
            rep.tp3 += 1
        if s.result == "SL":
            rep.sl += 1
        if s.result_points is not None:
            rep.points += s.result_points
    rep.avg_rr1 = sum(rr_list) / len(rr_list) if rr_list else 0.0
    rep.max_per_day = max(per_day.values(), default=0)
    rep.strengths, rep.skipped, rep.cancel_reasons, rep.sides = (
        dict(strengths), dict(skipped), dict(cancels), dict(sides))
    db.close()
    return rep


def format_report(r: Report, cfg: Settings) -> str:
    per_day = r.signals / r.days if r.days else 0.0
    lines = [
        f"Свечей M5: {r.bars} (~{r.days:.1f} дн.)",
        f"Сигналов: {r.signals} ({per_day:.2f} в день, максимум за день {r.max_per_day}, лимит {cfg.max_signals_day})",
        f"  по сторонам: {r.sides}; по силе: {r.strengths}",
        f"Вход состоялся: {r.entered}; отменено до входа: {r.cancelled_before_entry}; ещё открыто: {r.open_at_end}",
        f"Доля TP1 (от состоявшихся входов): {r.tp1_rate:.1%} ({r.tp1_or_better}/{r.entered}); "
        f"TP2+: {r.tp2_or_better}; TP3: {r.tp3}; SL без TP: {r.sl}",
        f"Средний RR до TP1 (план): 1:{r.avg_rr1:.2f}",
        f"Итог по плану: {r.points:+.1f} п. (лучший достигнутый TP; BE не моделируется)",
        f"Самая широкая зона: {r.max_zone_width:.1f} п. (ZONE_MAX {cfg.params.zone_max:g}); "
        f"зон шире ZONE_MAX: {r.zones_over_max}",
    ]
    if r.skipped:
        lines.append("Пропущено: " + "; ".join(f"{k} — {v}" for k, v in sorted(r.skipped.items(), key=lambda x: -x[1])))
    if r.cancel_reasons:
        lines.append("Отмены: " + "; ".join(f"{k} — {v}" for k, v in sorted(r.cancel_reasons.items(), key=lambda x: -x[1])))
    return "\n".join(lines)
