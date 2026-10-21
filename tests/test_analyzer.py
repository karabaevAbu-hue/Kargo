import dataclasses

import pytest
from scenario import P, build_buy, mirror
from xau_engine.engine.analyzer import analyze
from xau_engine.models import Side


def _ready(a):
    ready = a.ready
    assert len(ready) == 1, [(c.status, c.reason, c.key) for c in a.candidates]
    return ready[0]


def test_buy_scenario_produces_ready_signal():
    a = analyze(*build_buy(), P)
    assert a.bias.side is Side.BUY
    c = _ready(a)
    assert c.side is Side.BUY and c.strength == "A" and c.zstatus.status == "CONFIRMED"
    z = c.zone
    assert z.lo == pytest.approx(4100.0) and z.hi == pytest.approx(4105.0)
    assert z.sl == pytest.approx(4094.7)
    assert P.pts(z.hi - z.lo) <= P.zone_max
    assert c.plan.tps[0].price == pytest.approx(4119.0) and c.plan.rr[0] >= 2.0
    assert c.cycle is None  # точка 3 ещё не сформировалась
    assert [ok for ok, _ in c.confirmations][-1] is False


def test_sell_scenario_is_exact_mirror():
    buy = analyze(*build_buy(), P)
    sell = analyze(*mirror(*build_buy()), P)
    b, s = _ready(buy), _ready(sell)
    assert s.side is Side.SELL and s.strength == b.strength
    assert s.zone.lo == pytest.approx(-b.zone.hi) and s.zone.hi == pytest.approx(-b.zone.lo)
    assert s.zone.sl == pytest.approx(-b.zone.sl)
    assert [t.price for t in s.plan.tps] == pytest.approx([-t.price for t in b.plan.tps])


def test_wide_zone_is_skipped_not_cut():
    # при точности 0.1 $ за пункт 10 пунктов = 1 $: зона не помещается, сигнала нет
    p = dataclasses.replace(P, point_size=0.1, max_zone_distance=10_000)
    a = analyze(*build_buy(), p)
    assert not a.ready
    assert any(c.reason in ("wide", "no_cluster", "sl_far") for c in a.candidates)


def test_min_rr_filter_and_switch():
    strict = dataclasses.replace(P, min_rr=3.0)
    assert not analyze(*build_buy(), strict).ready
    off = dataclasses.replace(P, min_rr=None)
    assert analyze(*build_buy(), off).ready


def test_bias_mismatch_gives_watch_only():
    m5, d1, w = build_buy()
    d1 = [c.mirrored() for c in d1]
    w = [c.mirrored() for c in w]  # старшие ТФ медвежьи
    a = analyze(m5, d1, w, P)
    assert a.bias.side is Side.SELL
    assert not a.ready
    buy = [c for c in a.candidates if c.side is Side.BUY]
    assert buy and all(c.strength == "C" and c.status != "READY" for c in buy)


def test_no_signal_when_price_already_at_target():
    m5, d1, w = build_buy()
    from xau_engine.models import Candle

    last = m5[-1]
    extra = [Candle(last.t + 300 * (i + 1), 4109.2 + i * 2, 4109.2 + i * 2 + 2.2, 4109.0 + i * 2, 4109.2 + i * 2 + 2) for i in range(6)]
    a = analyze(m5 + extra, d1, w, P)
    assert not a.ready
