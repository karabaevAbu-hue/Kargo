"""График-скриншот сигнала с разметкой: тело OB, зона входа, SL, TP, Prev LOW/HIGH."""
from __future__ import annotations

import io
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from .engine.params import Params  # noqa: E402
from .models import Candle, Side  # noqa: E402
from .signals import Signal  # noqa: E402
from .timeutil import fmt_hm, to_bishkek  # noqa: E402

BG, FG, GRID = "#0f141a", "#d6dde6", "#232b35"
UP, DOWN = "#26a69a", "#ef5350"


def render_chart(
    sig: Signal,
    candles: Sequence[Candle],
    price: float | None = None,
    p: Params | None = None,
    tf_label: str = "M15",
    bars: int = 96,
) -> bytes:
    """PNG со свечами выбранного ТФ и разметкой сигнала."""
    c = list(candles)[-bars:]
    fig, ax = plt.subplots(figsize=(10, 6), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    width = 0.6
    for i, k in enumerate(c):
        col = UP if k.c >= k.o else DOWN
        ax.vlines(i, k.l, k.h, color=col, linewidth=1)
        ax.add_patch(Rectangle((i - width / 2, k.body_lo), width, max(k.body, 1e-9), color=col))

    n = len(c)
    right = n + 6
    zone_col = "#2e7d32" if sig.side is Side.BUY else "#c62828"
    if sig.ob_lo is not None and sig.ob_hi is not None:
        ax.axhspan(sig.ob_lo, sig.ob_hi, color="#78909c", alpha=0.25, zorder=0)
        ax.text(0.5, sig.ob_hi, f"OB {sig.ob_tf} (тело)", color="#90a4ae", fontsize=8, va="bottom")
    ax.axhspan(sig.zone_lo, sig.zone_hi, color=zone_col, alpha=0.35, zorder=1)
    ax.text(n + 0.5, (sig.zone_lo + sig.zone_hi) / 2, f"ENTRY {sig.zone_lo:.1f}–{sig.zone_hi:.1f}",
            color="#ffffff", fontsize=9, va="center", fontweight="bold")
    tag = "Prev. LOW" if sig.side is Side.BUY else "Prev. HIGH"
    ax.hlines(sig.true_stop, -1, n, color="#42a5f5", linestyle=":", linewidth=1)
    ax.text(0.5, sig.true_stop, f"{tag} {sig.true_stop:.1f}", color="#42a5f5", fontsize=8, va="bottom")
    ax.hlines(sig.sl, -1, n, color="#ff5252", linestyle="--", linewidth=1.2)
    ax.text(n + 0.5, sig.sl, f"SL {sig.sl:.1f}", color="#ff5252", fontsize=9, va="center")
    for i, tp in enumerate(sig.tps):
        ax.hlines(tp, -1, n, color="#66bb6a", linestyle="--", linewidth=1.0)
        ax.text(n + 0.5, tp, f"TP{i + 1} {tp:.1f}", color="#66bb6a", fontsize=9, va="center")
    if price is not None:
        ax.hlines(price, -1, n, color="#fdd835", linewidth=0.8, alpha=0.8)
        ax.text(n + 0.5, price, f"{price:.1f}", color="#fdd835", fontsize=8, va="center")

    lows = [k.l for k in c] + [sig.sl, sig.zone_lo]
    highs = [k.h for k in c] + [sig.zone_hi]
    if sig.tps:
        lows.append(min(sig.tps))
        highs.append(max(sig.tps))
    pad = (max(highs) - min(lows)) * 0.05
    ax.set_ylim(min(lows) - pad, max(highs) + pad)
    ax.set_xlim(-1, right + 6)
    step = max(1, n // 8)
    ax.set_xticks(range(0, n, step))
    ax.set_xticklabels([fmt_hm(c[i].t) for i in range(0, n, step)], color=FG, fontsize=8)
    ax.tick_params(axis="y", colors=FG, labelsize=8)
    ax.grid(color=GRID, linewidth=0.5)
    for sp in ax.spines.values():
        sp.set_color(GRID)
    day = to_bishkek(c[-1].t).strftime("%d.%m") if c else ""
    ax.set_title(f"XAUUSD {sig.side.value} · сила {sig.strength} · {tf_label} · {sig.feed} · {day} (Бишкек)",
                 color=FG, fontsize=11)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
