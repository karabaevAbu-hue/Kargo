"""Алгоритм анализа сверху вниз (раздел 3 ТЗ).

Сигнал рождается, только если сошлись: bias HTF, подтверждённая зона, крупная цель,
признак манипуляции и вход по циклу. Не хватает хотя бы одного — бот молчит
(или ставит зону «на наблюдение»).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..models import Candle, Side
from ..timeutil import trading_day_end
from .bias import Bias, compute_bias
from .candles import M5, TF_MINUTES, fvg_gaps, resample
from .cycle import Cycle, detect_cycle, detect_direct_entry
from .manipulation import Manip, collect_manipulations
from .orderblock import ZoneStatus, zone_status
from .params import Params
from .patterns import OrderBlock, find_order_blocks, find_retest_pairs
from .zone import Plan, Zone, ZoneFail, build_plan, build_zone

TF_SECONDS = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400, "W": 604800}
FRESH_ENTRY_CANDLES = 2  # точка 3 считается свежей, если сформировалась не раньше 2 M5 свечей назад
STRENGTH_ORDER = {"A+": 4, "A": 3, "B": 2, "C": 1}
OB_TFS = ("W", "D1", "H4", "H1")  # OB ищем только от Weekly до 1H
OB_LOOKBACK = {"W": 20, "D1": 40, "H4": 50, "H1": 80}
SKIP_TEXT = {
    "wide": "пропущено: широкая зона",
    "no_cluster": "пропущено: нет двух манипуляций в зоне",
    "sl_far": "пропущено: стоп дальше SL_MAX",
    "no_target": "пропущено: нет major-цели (LAL)",
    "rr": "пропущено: RR до TP1 ниже минимума",
    "imbalance": "пропущено: разворот из имбаланса",
    "level4": "пропущено: пробит Level #4",
    "target_reached": "пропущено: цена уже у цели",
    "entry_done": "пропущено: вход по циклу уже был",
    "zone_broken": "пропущено: зона пробита",
    "too_far": "пропущено: зона далеко от цены",
}


@dataclass
class Candidate:
    side: Side
    status: str  # READY | WATCH | SKIPPED
    strength: str  # A+ | A | B | C
    ob: OrderBlock
    zstatus: ZoneStatus
    key: str
    reason: str = ""
    zone: Zone | None = None
    plan: Plan | None = None
    manips: list[Manip] = field(default_factory=list)
    confirmations: list[tuple[bool, str]] = field(default_factory=list)
    best_sign: str = ""
    bias_ok: bool = False
    created_t: int = 0
    expires_t: int = 0
    feed: str = ""
    price: float = 0.0
    note: str = ""
    cycle: Cycle | None = None  # если точка 3 уже сформировалась к моменту анализа
    level4: float | None = None  # Level #4 HTF-пары: его пробой снимает зону

    @property
    def caution(self) -> bool:
        return self.strength == "B"

    @property
    def rank_key(self):
        return (
            self.status == "READY",
            STRENGTH_ORDER[self.strength],
            {"W": 4, "D1": 3, "H4": 2, "H1": 1}[self.ob.tf],
        )


@dataclass
class Analysis:
    bias: Bias
    price: float
    last_t: int
    candidates: list[Candidate]
    frames: dict[str, list[Candle]]

    @property
    def ready(self) -> list[Candidate]:
        return [c for c in self.candidates if c.status == "READY"]


def build_frames(
    m5: Sequence[Candle], d1: Sequence[Candle], w: Sequence[Candle], p: Params
) -> dict[str, list[Candle]]:
    frames: dict[str, list[Candle]] = {"M5": list(m5), "D1": list(d1), "W": list(w)}
    until = m5[-1].t + M5 if m5 else None
    for tf, minutes in TF_MINUTES.items():
        if tf == "M5":
            continue
        anchor = p.day_open_utc_hour if tf == "H4" else 0
        frames[tf] = resample(m5, minutes, anchor, until)
    return frames


def _bias_text(bias: Bias, side: Side) -> tuple[bool, str]:
    ok = bias.side is side
    tfs = "/".join(tf for tf in ("W", "D1", "H4") if tf in bias.votes)
    word = "бычий" if side is Side.BUY else "медвежий"
    if bias.negation:
        return ok, f"Bias {bias.negation[0]}: Negation, {word} поток" if ok else f"Bias {bias.describe()} не совпадает"
    return ok, f"Bias {tfs}: {word} поток" if ok else f"Bias {tfs}: {bias.describe()}, не совпадает с {side.value}"


def classify(manips: Sequence[Manip], bias_ok: bool) -> tuple[str, str]:
    """Сила сигнала по самой сильной манипуляции в зоне + совпадение с HTF."""
    hcs = [m for m in manips if m.kind == "HCS"]
    neg = [m for m in manips if m.kind == "NEG"]
    retest = [m for m in manips if m.kind == "RETEST"]
    chain = max([m.hcs_count for m in hcs], default=0)
    inside = any(m.inside_ob for m in hcs)
    if bias_ok and hcs and chain >= 2 and inside:
        return "A+", f"HCS x{min(chain, 3)} внутри HTF OB"
    if bias_ok and hcs:
        return "A", "HCS внутри OB" if inside else "HCS в зоне"
    if bias_ok and neg:
        return "A", "FU Negation x3" if any("x3" in m.detail for m in neg) else "FU Negation"
    if bias_ok and retest:
        return "B", "FU re-test"
    return "C", "только FU/AFU" if manips else "нет манипуляций"


def _recent_gap_hit(frames: Mapping[str, Sequence[Candle]], price: float, p: Params) -> str | None:
    """Разворот не начинается из имбаланса: уровень зоны не должен лежать внутри крупного FVG."""
    for tf in ("M5", "M15", "H1", "H4"):
        c = frames.get(tf)
        if not c:
            continue
        for g in fvg_gaps(c[-300:], p, tf, only_open=True):
            if g.lo <= price <= g.hi:
                return tf
    return None


def analyze(
    m5: Sequence[Candle],
    d1: Sequence[Candle],
    w: Sequence[Candle],
    p: Params,
    feed: str = "OANDA",
) -> Analysis:
    if len(m5) < 50:
        return Analysis(Bias(None), 0.0, 0, [], {})
    until = m5[-1].t + M5
    d1 = [c for c in d1 if c.t + TF_SECONDS["D1"] <= until]
    w = [c for c in w if c.t + TF_SECONDS["W"] <= until]
    frames = build_frames(m5, d1, w, p)
    last_t = m5[-1].t
    price = m5[-1].c
    bias = compute_bias(frames, p)
    tol = p.px(p.zone_max)
    out: list[Candidate] = []

    for tf in OB_TFS:
        series = frames.get(tf)
        if not series or len(series) < 8:
            continue
        for ob in find_order_blocks(series, tf, p, OB_LOOKBACK[tf]):
            side = ob.side
            # зона должна быть достижима: цена не ушла за дальнюю границу тела OB
            if (side is Side.BUY and price < ob.lo - tol) or (side is Side.SELL and price > ob.hi + tol):
                continue
            dist = max(0.0, price - ob.hi) if side is Side.BUY else max(0.0, ob.lo - price)
            key = f"{side.value}:{tf}:{ob.t}"
            st = zone_status(ob, frames, p)
            bias_ok = bias.side is side
            cand = Candidate(
                side=side, status="WATCH", strength="C", ob=ob, zstatus=st, key=key,
                created_t=last_t + M5, expires_t=trading_day_end(last_t + M5), feed=feed,
                price=price, bias_ok=bias_ok,
            )
            _, bias_line = _bias_text(bias, side)
            cand.confirmations.append((bias_ok, bias_line))
            cand.confirmations.append(
                (st.status == "CONFIRMED", f"OB {tf} ({ob.label}), зона {st.status}")
            )
            if p.pts(dist) > p.max_zone_distance:
                cand.status, cand.reason = "SKIPPED", "too_far"
                out.append(cand)
                continue
            if st.status != "CONFIRMED":
                cand.reason = "NEW/FRESH: ждём возврат и реакцию тенью"
                out.append(cand)
                continue

            manips = collect_manipulations(side, ob, frames, p)
            cand.manips = manips
            strength, sign = classify(manips, bias_ok)
            cand.strength, cand.best_sign = strength, sign
            cand.confirmations.append((strength != "C", sign if manips else "Признак манипуляции в зоне не найден"))

            zone = build_zone(side, manips, ob.lo, ob.hi, p)
            if isinstance(zone, ZoneFail):
                cand.status, cand.reason = "SKIPPED", zone.reason
                out.append(cand)
                continue
            cand.zone = zone

            # актуальность зоны
            if (side is Side.BUY and price < zone.sl) or (side is Side.SELL and price > zone.sl):
                cand.status, cand.reason = "SKIPPED", "zone_broken"
                out.append(cand)
                continue

            fu_c = series[ob.fu.idx]
            htf_edge = fu_c.h if side is Side.BUY else fu_c.l
            plan = build_plan(zone, frames, htf_edge, p)
            if plan is None:
                cand.status, cand.reason = "SKIPPED", "no_target"
                out.append(cand)
                continue
            cand.plan = plan
            tp1 = plan.tps[0]
            cand.confirmations.append((True, f"Цель: LAL — {tp1.label(side)}"))

            if (price - tp1.price) * side.sign >= 0:
                cand.status, cand.reason = "SKIPPED", "target_reached"
                out.append(cand)
                continue
            if p.min_rr is not None and plan.rr[0] < p.min_rr:
                cand.status, cand.reason = "SKIPPED", "rr"
                out.append(cand)
                continue
            anchor_level = zone.lo if side is Side.BUY else zone.hi
            gap_tf = _recent_gap_hit(frames, anchor_level, p)
            if gap_tf:
                cand.status, cand.reason, cand.note = "SKIPPED", "imbalance", gap_tf
                out.append(cand)
                continue

            # уровни ретеста HTF-пары Doji + большая свеча
            level_note = ""
            blocked = False
            for ptf in ("H1", "H4", "D1"):
                pc = frames.get(ptf)
                if not pc:
                    continue
                for pair in find_retest_pairs(pc, ptf, side, p)[:3]:
                    for n_lvl, lvl in enumerate(pair.levels, start=1):
                        if zone.lo - tol <= lvl <= zone.hi + tol:
                            if pair.broken(pc):
                                blocked = True
                            else:
                                level_note = f"Уровень ретеста HTF #{n_lvl} ({ptf})"
                                cand.level4 = pair.levels[3]
            if blocked:
                cand.status, cand.reason = "SKIPPED", "level4"
                out.append(cand)
                continue
            if level_note:
                cand.confirmations.append((True, level_note))

            # цикл считаем только после закрытия свечи последней манипуляции
            after_t = max((m.t + TF_SECONDS[m.tf] for m in manips), default=0)
            m5_after = [c for c in frames["M5"] if c.t >= after_t]
            cy = detect_cycle(m5_after, side, zone.lo, zone.hi)
            if not cy.entered:
                dcy = detect_direct_entry(m5_after, side, zone.lo, zone.hi, p)
                cy = dcy or cy
            if cy.entered:
                near = zone.lo - (zone.hi - zone.lo) <= price <= zone.hi + (zone.hi - zone.lo)
                fresh = cy.p3_t is not None and last_t - cy.p3_t <= FRESH_ENTRY_CANDLES * M5
                if not (fresh and near and cy.in_zone):
                    cand.status, cand.reason = "SKIPPED", "entry_done"
                    out.append(cand)
                    continue
                cand.cycle = cy

            level = zone.lo if side is Side.BUY else zone.hi
            word = "выше" if side is Side.BUY else "ниже"
            tag = "Prev. LOW" if side is Side.BUY else "Prev. HIGH"
            if cand.cycle:
                how = "прямой вход BIW+Doji" if cand.cycle.direct else f"точка 3, закрытие {cand.cycle.p3_close:.1f}"
                cand.confirmations.append((True, f"Вход M5: {how}"))
            else:
                cand.confirmations.append(
                    (False, f"Вход M5: жди точку 3 (закрытие {word} {tag} {level:.1f})")
                )
            cand.status = "READY" if strength in ("A+", "A", "B") else "WATCH"
            if cand.status == "WATCH":
                cand.reason = "класс C: только на наблюдение"
            out.append(cand)

    out.sort(key=lambda c: c.rank_key, reverse=True)
    return Analysis(bias, price, last_t, out, frames)
