"""Сигнал: запись, статусы и фабрика из результата анализа."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .engine.analyzer import Candidate
from .engine.candles import TF_RANK
from .engine.params import Params
from .models import Side
from .timeutil import day_key

# статусы
WATCH, PENDING, IN_ZONE, ENTRY, TP1, TP2 = "WATCH", "PENDING", "IN_ZONE", "ENTRY", "TP1", "TP2"
TP3, SL, CANCELLED, EXPIRED, SKIPPED = "TP3", "SL", "CANCELLED", "EXPIRED", "SKIPPED"
ACTIVE = (PENDING, IN_ZONE, ENTRY, TP1, TP2)
CLOSED = (TP3, SL, CANCELLED, EXPIRED)
TRADE_STATES = (ENTRY, TP1, TP2)  # вход состоялся

STATUS_RU = {
    WATCH: "на наблюдении",
    PENDING: "ждёт входа цены в зону",
    IN_ZONE: "цена в зоне, ждём точку 3",
    ENTRY: "условие входа выполнено",
    TP1: "TP1 достигнут",
    TP2: "TP2 достигнут",
    TP3: "TP3 достигнут",
    SL: "Stop Loss",
    CANCELLED: "отменён",
    EXPIRED: "истёк срок",
    SKIPPED: "пропущено",
}


def r1(x: float) -> str:
    """Округление до 0.1 «как в школе» (15.75 -> 15.8), без банковского округления."""
    q = Decimal(str(round(x, 6))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return str(q)


@dataclass
class Signal:
    key: str
    side: Side
    kind: str  # SIGNAL | WATCH | SKIPPED
    status: str
    strength: str
    zone_lo: float
    zone_hi: float
    true_stop: float
    sl: float
    tps: list[float] = field(default_factory=list)
    tp_labels: list[str] = field(default_factory=list)
    confirmations: list[tuple[bool, str]] = field(default_factory=list)
    tfs: list[str] = field(default_factory=list)
    feed: str = ""
    created_t: int = 0
    expires_t: int = 0
    track_from_t: int = 0
    level4: float | None = None
    ob_lo: float | None = None
    ob_hi: float | None = None
    ob_tf: str = ""
    ob_label: str = ""
    best_sign: str = ""
    reason: str = ""
    source: str = "B"  # A — индикатор TradingView, B — свой расчёт
    # состояние сделки
    entered_zone_t: int | None = None
    entry_t: int | None = None
    entry_direct: bool = False
    tps_hit: int = 0
    closed_t: int | None = None
    result: str = ""
    result_points: float | None = None
    sl_initial: float | None = None
    id: int | None = None

    # --- производные величины (всё считается от середины зоны, раздел 4 ТЗ)
    @property
    def mid(self) -> float:
        return (self.zone_lo + self.zone_hi) / 2

    @property
    def risk(self) -> float:
        return abs(self.mid - self.sl)

    def width_pts(self, p: Params) -> float:
        return p.pts(self.zone_hi - self.zone_lo)

    def dist_pts(self, price: float, p: Params) -> float:
        return p.pts((price - self.mid) * self.side.sign)

    def rr(self, i: int) -> float:
        return abs(self.tps[i] - self.mid) / self.risk if self.risk > 0 else 0.0

    @property
    def active(self) -> bool:
        return self.kind == "SIGNAL" and self.status in ACTIVE

    @property
    def entered(self) -> bool:
        return self.status in TRADE_STATES or (self.kind == "SIGNAL" and self.entry_t is not None)

    @property
    def day(self) -> str:
        return day_key(self.created_t)

    def to_json(self) -> str:
        d = asdict(self)
        d["side"] = self.side.value
        return json.dumps(d, ensure_ascii=False)

    @staticmethod
    def from_json(raw: str, id_: int | None = None) -> "Signal":
        d = json.loads(raw)
        d["side"] = Side(d["side"])
        d["confirmations"] = [tuple(x) for x in d.get("confirmations", [])]
        sig = Signal(**d)
        if id_ is not None:
            sig.id = id_
        return sig


def result_points(sig: Signal, p: Params) -> float:
    """Итог в пунктах от середины зоны: лучший достигнутый TP, иначе -риск (стоп)."""
    if sig.tps_hit > 0:
        return p.pts(abs(sig.tps[sig.tps_hit - 1] - sig.mid))
    return -p.pts(abs(sig.mid - sig.sl_initial) if sig.sl_initial is not None else sig.risk)


def from_candidate(c: Candidate, kind: str, source: str = "B") -> Signal:
    """Сигнал / наблюдение / пропуск из результата анализа."""
    z, plan = c.zone, c.plan
    tfs = {"D1", "H4", c.ob.tf, "M5"} | {m.tf for m in c.manips}
    tfs_sorted = sorted(tfs, key=lambda t: -TF_RANK[t])
    if z is not None:
        lo, hi, ts, sl = z.lo, z.hi, z.true_stop, z.sl
    else:  # наблюдение: показываем тело OB
        lo, hi = c.ob.lo, c.ob.hi
        ts = lo if c.side is Side.BUY else hi
        sl = lo if c.side is Side.BUY else hi
    status = {"SIGNAL": PENDING, "WATCH": WATCH, "SKIPPED": SKIPPED}[kind]
    sig = Signal(
        key=c.key,
        side=c.side,
        kind=kind,
        status=status,
        strength=c.strength,
        zone_lo=lo,
        zone_hi=hi,
        true_stop=ts,
        sl=sl,
        tps=[t.price for t in plan.tps] if plan else [],
        tp_labels=[t.label(c.side) for t in plan.tps] if plan else [],
        confirmations=list(c.confirmations),
        tfs=tfs_sorted,
        feed=c.feed,
        created_t=c.created_t,
        expires_t=c.expires_t,
        track_from_t=c.created_t,
        level4=c.level4,
        ob_lo=c.ob.lo,
        ob_hi=c.ob.hi,
        ob_tf=c.ob.tf,
        ob_label=c.ob.label,
        best_sign=c.best_sign,
        reason=c.reason,
        source=source,
        sl_initial=sl,
    )
    if c.cycle is not None and kind == "SIGNAL":  # точка 3 уже есть на момент отправки
        sig.status = ENTRY
        sig.entered_zone_t = c.cycle.p1_t
        sig.entry_t = c.cycle.p3_t
        sig.entry_direct = c.cycle.direct
    return sig
