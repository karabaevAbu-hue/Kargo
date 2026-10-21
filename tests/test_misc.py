import asyncio

import httpx
import pytest
from scenario import P, build_buy
from test_tracker import path
from xau_engine.backtest import derive_htf, format_report, load_csv, run_backtest, save_csv, synthetic_m5
from xau_engine.chart import render_chart
from xau_engine.config import Settings
from xau_engine.engine.analyzer import analyze
from xau_engine.engine.candles import resample
from xau_engine.feed.runner import closed_only, market_closed
from xau_engine.feed.twelvedata import FeedError, TwelveDataClient, parse_values
from xau_engine.signals import from_candidate
from xau_engine.texts import active_text, analysis_text, history_text, welcome_text
from xau_engine.tracker import simulate


def td_payload(rows, status="ok"):
    return {"meta": {"symbol": "XAU/USD"}, "values": rows, "status": status}


def client_with(handler):
    return TwelveDataClient("key", "XAU/USD", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_twelvedata_parses_and_sorts_utc(monkeypatch):
    monkeypatch.setattr("xau_engine.feed.twelvedata.MIN_GAP_S", 0)
    seen = {}

    def handler(req: httpx.Request):
        seen.update(dict(req.url.params))
        return httpx.Response(200, json=td_payload([
            {"datetime": "2026-10-21 10:05:00", "open": "4101.5", "high": "4102", "low": "4101", "close": "4101.8"},
            {"datetime": "2026-10-21 10:00:00", "open": "4100", "high": "4102", "low": "4099.5", "close": "4101.5"},
        ]))

    c = client_with(handler)
    out = asyncio.run(c.series("M5", 2))
    assert [x.t for x in out] == sorted(x.t for x in out) and out[0].o == 4100.0
    assert seen["interval"] == "5min" and seen["timezone"] == "UTC" and seen["symbol"] == "XAU/USD"
    assert parse_values([{"datetime": "2026-10-21", "open": "1", "high": "2", "low": "0.5", "close": "1.5"}], True)[0].t == 1792540800


def test_twelvedata_error_and_missing_key(monkeypatch):
    monkeypatch.setattr("xau_engine.feed.twelvedata.MIN_GAP_S", 0)
    c = client_with(lambda r: httpx.Response(200, json={"code": 429, "message": "limit", "status": "error"}))
    with pytest.raises(FeedError):
        asyncio.run(c.series("D1", 10))
    with pytest.raises(FeedError):
        TwelveDataClient("")


def test_twelvedata_history_pages_back(monkeypatch):
    monkeypatch.setattr("xau_engine.feed.twelvedata.MIN_GAP_S", 0)
    calls = []

    def handler(req):
        calls.append(req.url.params.get("end_date"))
        if len(calls) == 1:
            rows = [{"datetime": f"2026-10-21 10:{m:02d}:00", "open": "1", "high": "2", "low": "0.5", "close": "1.5"} for m in (55, 50)]
        elif len(calls) == 2:
            rows = [{"datetime": "2026-10-21 09:00:00", "open": "1", "high": "2", "low": "0.5", "close": "1.5"}]
        else:
            rows = []
        return httpx.Response(200, json=td_payload(rows))

    out = asyncio.run(client_with(handler).history("M5", since_t=0))
    assert len(out) == 3 and calls[0] is None and calls[1] == "2026-10-21 10:49:59"


def test_closed_only_and_weekend_detection():
    from xau_engine.models import Candle

    cs = [Candle(0, 1, 1, 1, 1), Candle(300, 1, 1, 1, 1)]
    assert len(closed_only(cs, 300, 599)) == 1 and len(closed_only(cs, 300, 600)) == 2
    # 2026-10-24 — суббота, 2026-10-21 — среда
    assert market_closed(1792800000) and not market_closed(1792580000)


def test_chart_renders_png():
    m5, d1, w = build_buy()
    a = analyze(m5, d1, w, P)
    sig = from_candidate(a.ready[0], "SIGNAL")
    png = render_chart(sig, resample(m5, 15), a.price, P)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000


def test_backtest_pipeline_on_scenario_and_csv_roundtrip(tmp_path):
    m5, d1, w = build_buy()
    cfg = Settings(params=P)
    bars = path(m5[-1].t + 300, m5[-1].c, [4103.0, 4098.0, 4101.5, 4120.0, 4123.0])
    rep = run_backtest(m5 + bars, d1, w, cfg, warmup=300)
    assert rep.signals == 1 and rep.zones_over_max == 0 and rep.max_zone_width <= P.zone_max
    assert "Сигналов: 1" in format_report(rep, cfg)
    f = tmp_path / "m5.csv"
    save_csv(f, m5[:50])
    back = load_csv(f)
    assert len(back) == 50 and back[0].t == m5[0].t and abs(back[3].c - m5[3].c) < 0.01


def test_backtest_invariants_on_random_walk():
    m5 = synthetic_m5(6, seed=3)
    d1, w = derive_htf(m5, 21)
    cfg = Settings(params=P)
    rep = run_backtest(m5, d1, w, cfg, warmup=600, stride=6)
    assert rep.zones_over_max == 0 and rep.max_per_day <= cfg.max_signals_day


def test_csv_loader_validates_columns(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError):
        load_csv(f)


def test_texts_for_lists_and_analysis():
    m5, d1, w = build_buy()
    a = analyze(m5, d1, w, P)
    sig = from_candidate(a.ready[0], "SIGNAL")
    sig.id = 1
    txt = active_text([sig], a.price, P)
    assert "ждёт входа цены в зону" in txt and "до зоны" in txt
    assert "Активных сигналов нет" in active_text([], None, P)
    hist = history_text([sig], {"n": 2, "wins": 1, "losses": 1, "points": 3.4}, {"n": 5, "wins": 3, "losses": 2, "points": 10.0}, P)
    assert "За день: 2 закрыто" in hist and "+3.4" in hist
    an = analysis_text(a, P, "OANDA", m5[-1].t)
    assert "Bias: бычий" in an and "4100.0 - 4105.0" in an and "Это разбор, а не сигнал" in an
    assert "мало" in analysis_text(analyze([], [], [], P), P, "OANDA", 0)
    assert "2–3 сигнала" in welcome_text(3, "OANDA")


def test_sl_message_after_tp1_and_cancel_texts():
    from xau_engine.texts import event_text

    m5, d1, w = build_buy()
    sig = from_candidate(analyze(m5, d1, w, P).ready[0], "SIGNAL")
    bars = path(m5[-1].t + 300, m5[-1].c, [4103.0, 4098.0, 4101.5, 4119.5, 4090.0])
    s, evs = simulate(sig, bars, P, min_rr=2.0)
    texts = {e.kind: event_text(s, e, P) for e in evs}
    assert "Можно перенести стоп в безубыток" in texts["TP1"]
    assert "Стоп сработал после TP1" in texts["SL"]
    s2, evs2 = simulate(sig, path(m5[-1].t + 300, m5[-1].c, [4125.0]), P, min_rr=2.0)
    assert event_text(s2, evs2[0], P).startswith("🚫 Отменён: цена ушла к цели без входа")


def test_signal_text_matches_tz_example_exactly():
    """Раздел 5 ТЗ: формат сигнала и цифры примера (условные цифры из раздела 4)."""
    import html
    import re
    from datetime import datetime

    from xau_engine.models import Side
    from xau_engine.signals import PENDING, Signal
    from xau_engine.texts import signal_text
    from xau_engine.timeutil import BISHKEK

    exp = int(datetime(2026, 10, 21, 23, 0, tzinfo=BISHKEK).timestamp())
    s = Signal(
        key="k", side=Side.BUY, kind="SIGNAL", status=PENDING, strength="A", zone_lo=4100.0, zone_hi=4104.5,
        true_stop=4100.0, sl=4094.7, tps=[4118.0, 4131.0, 4146.0],
        confirmations=[(True, "Bias H4/D1: бычий поток"), (True, "OB H1 (Sell to Buy), зона CONFIRMED"),
                       (True, "HCS внутри OB"), (True, "Цель: LAL — хай Doji H1"),
                       (False, "Вход M5: жди точку 3 (закрытие выше Prev. LOW 4100.0)")],
        tfs=["D1", "H4", "H1", "M15", "M5"], feed="OANDA", created_t=exp - 3600, expires_t=exp,
    )
    text = html.unescape(re.sub(r"</?pre>", "", signal_text(s, P)))
    assert text == (
        "🟢 XAUUSD — BUY            Сила: A\n\n"
        "Entry Zone: 4100.0 - 4104.5  (4.5 п.)\n"
        "Stop Loss:  4094.7           (-7.6 п. от середины)\n"
        "TP1: 4118.0  (+15.8 п.)  RR 1:2.1\n"
        "TP2: 4131.0  (+28.8 п.)  RR 1:3.8\n"
        "TP3: 4146.0  (+43.8 п.)  RR 1:5.8\n\n"
        "Подтверждения:\n"
        "✅ Bias H4/D1: бычий поток\n"
        "✅ OB H1 (Sell to Buy), зона CONFIRMED\n"
        "✅ HCS внутри OB\n"
        "✅ Цель: LAL — хай Doji H1\n"
        "⬜ Вход M5: жди точку 3 (закрытие выше Prev. LOW 4100.0)\n\n"
        "ТФ: D1 · H4 · H1 · M15 · M5   Фид: OANDA\n"
        "Действует до: 21.10 23:00 (Бишкек)\n"
        "⚠️ Жди подтверждение входа на M5. Это не гарантия."
    )
    sell = html.unescape(signal_text(dataclasses_replace(s), P))
    assert "🔴 XAUUSD — SELL" in sell


def dataclasses_replace(s):
    import dataclasses

    from xau_engine.models import Side

    return dataclasses.replace(s, side=Side.SELL, strength="B")
