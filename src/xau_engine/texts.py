"""Тексты сообщений бота (HTML для Telegram). Время везде — Бишкек (UTC+6)."""
from __future__ import annotations

from html import escape
from typing import Sequence

from .engine.analyzer import SKIP_TEXT, Analysis, Candidate
from .engine.params import Params
from .models import Side
from .signals import (
    STATUS_RU,
    Signal,
    r1,
)
from .timeutil import fmt_dt, fmt_hm
from .tracker import TrackEvent

DISCLAIMER = "Это не гарантия."


def p1(x: float) -> str:
    return f"{x:.1f}"


def signed(x: float) -> str:
    return f"+{r1(x)}" if x >= 0 else f"-{r1(abs(x))}"


def dot(side: Side) -> str:
    return "🟢" if side is Side.BUY else "🔴"


def _zone_line(sig: Signal, p: Params) -> str:
    return f"{p1(sig.zone_lo)} - {p1(sig.zone_hi)}  ({r1(sig.width_pts(p))} п.)"


def signal_text(sig: Signal, p: Params) -> str:
    """Формат сигнала из раздела 5 ТЗ (моноширинный блок)."""
    strength = f"Сила: {sig.strength}" + ("  ⚠️ осторожно" if sig.strength == "B" else "")
    head = f"{dot(sig.side)} XAUUSD — {sig.side.value}"
    lines = [f"{head:<26}{strength}", ""]
    lines.append(f"Entry Zone: {_zone_line(sig, p)}")
    risk_pts = p.pts(sig.risk)
    lines.append(f"Stop Loss:  {p1(sig.sl):<17}(-{r1(risk_pts)} п. от середины)")
    for i, tp in enumerate(sig.tps):
        dist = p.pts(abs(tp - sig.mid))
        lines.append(f"TP{i + 1}: {p1(tp)}  (+{r1(dist)} п.)  RR 1:{r1(sig.rr(i))}")
    lines += ["", "Подтверждения:"]
    for ok, text in sig.confirmations:
        lines.append(f"{'✅' if ok else '⬜'} {text}")
    lines += [
        "",
        f"ТФ: {' · '.join(sig.tfs)}   Фид: {sig.feed}",
        f"Действует до: {fmt_dt(sig.expires_t)} (Бишкек)",
    ]
    entered = sig.entry_t is not None
    lines.append(
        f"⚠️ {'Условие входа уже выполнено. ' if entered else 'Жди подтверждение входа на M5. '}{DISCLAIMER}"
    )
    return f"<pre>{escape(chr(10).join(lines))}</pre>"


def watch_text(sig: Signal, p: Params) -> str:
    where = f"{p1(sig.zone_lo)} - {p1(sig.zone_hi)}"
    why = sig.reason or "зона на наблюдении"
    lines = [
        f"👀 Зона на наблюдении — {dot(sig.side)} {sig.side.value}",
        f"OB {sig.ob_tf} ({sig.ob_label}): {where}",
        f"Статус: {why}",
        f"Фид: {sig.feed}. Входа нет, это не сигнал.",
    ]
    return "\n".join(escape(x) for x in lines)


def event_text(sig: Signal, ev: TrackEvent, p: Params) -> str:
    side = f"{dot(sig.side)} XAUUSD {sig.side.value}"
    k = ev.kind
    if k == "ZONE":
        return f"📍 Цена в зоне, жди точку 3 на M5\n{side}: {p1(sig.zone_lo)} - {p1(sig.zone_hi)}"
    if k == "ENTRY":
        way = "прямой вход (BIW + Doji)" if sig.entry_direct else "точка 3 цикла манипуляции"
        out = [f"🎯 Условие входа выполнено — {way}", f"{side}: цена {p1(ev.price)}"]
        out.append(f"SL {p1(sig.sl)}  (-{r1(p.pts(sig.risk))} п. от середины зоны)")
        if sig.tps:
            out.append("TP: " + " / ".join(p1(t) for t in sig.tps))
        if sig.sl != sig.sl_initial and sig.sl_initial is not None:
            out.append(f"Стоп уточнён по выносу: было {p1(sig.sl_initial)}")
        out.append("Сделку открываешь сам, вручную.")
        return "\n".join(out)
    if k in ("TP1", "TP2", "TP3"):
        out = f"✅ {k} {signed(ev.points or 0)} п.  ({p1(ev.price)})\n{side}"
        if k == "TP1":
            out += "\nМожно перенести стоп в безубыток."
        if sig.status == "TP3":
            out += "\nВсе цели достигнуты, сигнал закрыт."
        return out
    if k == "SL":
        if sig.tps_hit:
            return (f"❌ Стоп сработал после TP{sig.tps_hit}\n{side}\n"
                    f"Итог по плану: {signed(sig.result_points or 0)} п. (лучший TP{sig.tps_hit})")
        return f"❌ Stop Loss {signed(ev.points or -p.pts(sig.risk))} п.\n{side}"
    if k == "EXPIRED":
        return f"🚫 Отменён: {ev.reason}\n{side}"
    return f"🚫 Отменён: {ev.reason}\n{side}"


def _result_short(sig: Signal) -> str:
    if sig.kind == "SKIPPED":
        return SKIP_TEXT.get(sig.reason, "пропущено")
    if sig.result:
        pts = f" {signed(sig.result_points)} п." if sig.result_points is not None else ""
        return f"{sig.result}{pts}"
    return STATUS_RU.get(sig.status, sig.status)


def active_text(signals: Sequence[Signal], price: float | None, p: Params, watch: Sequence[Signal] = ()) -> str:
    if not signals and not watch:
        return "Активных сигналов нет. Нет зоны — нет сигнала."
    out = ["<b>📊 Активные сигналы</b>"]
    for s in signals:
        row = (f"\n{dot(s.side)} {s.side.value} · сила {s.strength} · зона {p1(s.zone_lo)}-{p1(s.zone_hi)}\n"
               f"SL {p1(s.sl)} · TP1 {p1(s.tps[0]) if s.tps else '—'}\n"
               f"Статус: {STATUS_RU.get(s.status, s.status)} · до {fmt_hm(s.expires_t)}")
        if price is not None:
            if s.entered:
                row += f"\nЦена {p1(price)}: {signed(s.dist_pts(price, p))} п. от входа"
            else:
                gap = (price - s.zone_hi) if s.side is Side.BUY else (s.zone_lo - price)
                row += f"\nЦена {p1(price)}: до зоны {r1(max(0.0, p.pts(gap)))} п."
        out.append(escape(row))
    if watch:
        out.append("\n<b>👀 На наблюдении</b>")
        for s in watch:
            out.append(escape(f"{dot(s.side)} {s.side.value} OB {s.ob_tf}: {p1(s.zone_lo)}-{p1(s.zone_hi)}"))
    return "\n".join(out)


def history_text(signals: Sequence[Signal], day: dict, week: dict, p: Params) -> str:
    out = ["<b>📜 История (последние 20)</b>"]
    if not signals:
        out.append("Пока пусто.")
    for s in signals:
        out.append(escape(f"{fmt_dt(s.created_t)} {dot(s.side)} {s.side.value} {s.strength} · {_result_short(s)}"))

    def line(name: str, d: dict) -> str:
        return f"{name}: {d['n']} закрыто, {d['wins']} плюс / {d['losses']} минус, {signed(d['points'])} п."

    out += ["", escape(line("За день", day)), escape(line("За неделю", week))]
    return "\n".join(out)


def settings_text(s: dict, quiet_start: int, quiet_end: int, max_day: int) -> str:
    rr = "выкл" if s["min_rr"] is None else f"1:{s['min_rr']:g}"
    return (
        "<b>⚙️ Настройки</b>\n"
        f"Ширина зоны: до {s['zone_max']} п.\n"
        f"Минимальный RR до TP1: {rr}\n"
        f"«На наблюдении»: {'присылать' if s['send_watch'] else 'не присылать'}\n"
        f"Тихие часы {quiet_start:02d}:00–{quiet_end:02d}:00 (Бишкек): "
        f"{'без звука' if s['quiet'] else 'выкл'}\n"
        f"Лимит сигналов в день: {max_day}"
    )


def analysis_text(a: Analysis, p: Params, feed: str, ts: int) -> str:
    if not a.last_t:
        return "Данных пока мало для разбора. Подожди накопления свечей."
    out = [f"<b>🔍 Анализ сейчас</b> · {fmt_dt(ts)} · Фид: {escape(feed)}",
           f"Цена: {p1(a.price)}",
           f"Bias: {escape(a.bias.describe())}"]
    for side in (Side.BUY, Side.SELL):
        c = _best_for(a.candidates, side)
        title = f"{dot(side)} {side.value}"
        if c is None:
            out.append(f"\n{title}: зоны нет")
            continue
        z = (f"{p1(c.zone.lo)} - {p1(c.zone.hi)}" if c.zone else f"OB {p1(c.ob.lo)} - {p1(c.ob.hi)}")
        state = {"READY": "готов сигнал", "WATCH": "на наблюдении", "SKIPPED": SKIP_TEXT.get(c.reason, "пропущено")}[c.status]
        line = f"\n{title}: {z} · OB {c.ob.tf} · {c.zstatus.status} · сила {c.strength} · {state}"
        if c.plan:
            tps = " / ".join(p1(t.price) for t in c.plan.tps)
            line += f"\nЦели: {tps}  (RR1 1:{r1(c.plan.rr[0])})"
        out.append(escape(line))
    out.append("\nЭто разбор, а не сигнал. Сигнал приходит только по подтверждённой зоне.")
    return "\n".join(out)


def _best_for(cands: Sequence[Candidate], side: Side) -> Candidate | None:
    pool = [c for c in cands if c.side is side]
    if not pool:
        return None
    pool.sort(key=lambda c: (c.status == "READY", c.status == "WATCH" and c.zstatus.status == "CONFIRMED", c.rank_key), reverse=True)
    return pool[0]


def welcome_text(max_day: int, feed: str) -> str:
    return (
        "Привет. Я XAU Engine Pro: анализирую XAUUSD по стратегии Mr. Casino и присылаю зону входа "
        "BUY или SELL шириной не больше 10 пунктов (цель — 5), со стопом и тейками.\n\n"
        f"Работаю автоматически: примерно 2–3 сигнала в день (максимум {max_day}). "
        "Нет подтверждённой зоны — нет сигнала.\n"
        "Сделки я не открываю: вход вручную, на твоё усмотрение.\n"
        f"Фид расчёта: {escape(feed)}. Время — Бишкек (UTC+6).\n\n"
        "Каждый сигнал — вероятность, а не гарантия."
    )
