import dataclasses

import pytest
from scenario import P, build_buy, mirror
from xau_engine.engine.analyzer import analyze
from xau_engine.models import Candle, Side
from xau_engine.signals import (
    CANCELLED, ENTRY, EXPIRED, PENDING, SL, from_candidate, r1,
)
from xau_engine.tracker import simulate


def path(start_t, last, pts, step_n=3):
    """M5 свечи по ломаной last -> pts[0] -> pts[1]..., step_n свечей на сегмент."""
    out, t, px = [], start_t, last
    for target in pts:
        for i in range(1, step_n + 1):
            c = px + (target - px) * i / step_n
            o = px + (target - px) * (i - 1) / step_n
            out.append(Candle(t, o, max(o, c), min(o, c), c))
            t += 300
        px = target
    return out


@pytest.fixture
def setup():
    m5, d1, w = build_buy()
    a = analyze(m5, d1, w, P)
    sig = from_candidate(a.ready[0], "SIGNAL")
    return m5, sig


def run(setup, pts, **kw):
    m5, sig = setup
    bars = path(m5[-1].t + 300, m5[-1].c, pts)
    return simulate(sig, bars, P, min_rr=2.0, **kw)


def kinds(evs):
    return [e.kind for e in evs]


def test_full_cycle_to_tp1_tp2(setup):
    # вниз в зону (P1), вынос ниже 4100.0 (P2), закрытие выше (P3), затем к целям
    s, evs = run(setup, [4103.0, 4098.0, 4101.5, 4120.0, 4123.0])
    assert kinds(evs)[:2] == ["ZONE", "ENTRY"]
    assert "TP1" in kinds(evs)
    assert s.entry_t is not None and s.tps_hit >= 1


def test_entry_after_p3_with_deeper_stop(setup):
    s, evs = run(setup, [4103.0, 4096.0, 4101.5])
    assert kinds(evs) == ["ZONE", "ENTRY"] and s.status == ENTRY
    assert s.sl == pytest.approx(4096.0 - 1.5) or s.sl == pytest.approx(s.sl_initial)
    assert s.sl <= s.sl_initial


def test_cancel_when_price_runs_to_tp1_without_entry(setup):
    s, evs = run(setup, [4121.0])
    assert kinds(evs) == ["CANCEL"] and "без входа" in evs[0].reason and s.status == CANCELLED


def test_cancel_when_stop_broken_before_entry(setup):
    s, evs = run(setup, [4103.0, 4090.0])
    assert "CANCEL" in kinds(evs) and s.status == CANCELLED


def test_sl_after_entry_is_loss_in_points(setup):
    s, evs = run(setup, [4103.0, 4098.0, 4101.5, 4090.0])
    assert kinds(evs)[-1] == "SL" and s.status == SL and s.result == "SL"
    assert s.result_points < 0


def test_sl_after_tp1_keeps_tp_result(setup):
    s, evs = run(setup, [4103.0, 4098.0, 4101.5, 4119.5, 4090.0])
    assert "TP1" in kinds(evs) and kinds(evs)[-1] == "SL"
    assert s.result == "TP1" and s.result_points > 0


def test_expiry_before_entry(setup):
    m5, sig = setup
    sig = dataclasses.replace(sig, expires_t=m5[-1].t + 900)
    bars = path(m5[-1].t + 300, m5[-1].c, [4108.0, 4109.0], step_n=6)
    s, evs = simulate(sig, bars, P, min_rr=2.0)
    assert kinds(evs) == ["EXPIRED"] and s.status == EXPIRED


def test_simulation_is_deterministic_and_incremental(setup):
    m5, sig = setup
    bars = path(m5[-1].t + 300, m5[-1].c, [4103.0, 4098.0, 4101.5, 4120.0])
    full_s, full_evs = simulate(sig, bars, P, min_rr=2.0)
    part_s, part_evs = simulate(sig, bars[:8], P, min_rr=2.0)
    assert kinds(full_evs)[: len(part_evs)] == kinds(part_evs)
    assert simulate(sig, bars, P, min_rr=2.0)[1] == full_evs


def test_sell_mirror_gives_same_events():
    m5, d1, w = build_buy()
    sell_sig = from_candidate(analyze(*mirror(m5, d1, w), P).ready[0], "SIGNAL")
    assert sell_sig.side is Side.SELL
    bars = [c.mirrored() for c in path(m5[-1].t + 300, m5[-1].c, [4103.0, 4098.0, 4101.5, 4120.0, 4123.0])]
    _, evs = simulate(sell_sig, bars, P, min_rr=2.0)
    assert kinds(evs)[:3] == ["ZONE", "ENTRY", "TP1"]


def test_pending_state_before_price_reaches_zone(setup):
    s, evs = run(setup, [4108.0])
    assert evs == [] and s.status == PENDING


def test_rounding_half_up_matches_tz_example():
    # раздел 4 ТЗ: +15.75 -> 15.8, 7.55 -> 7.6
    assert r1(15.75) == "15.8" and r1(7.55) == "7.6" and r1(28.75) == "28.8" and r1(43.75) == "43.8"
