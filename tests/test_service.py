
import pytest
from scenario import P, build_buy
from test_tracker import path
from xau_engine.config import Settings
from xau_engine.db import Database
from xau_engine.service import Service, parse_time


def make_service(mode="B", **kw):
    db = Database(":memory:")
    cfg = Settings(mode=mode, params=P, **kw)
    return Service(db, cfg), db


def load(svc):
    m5, d1, w = build_buy()
    svc.ingest("M5", m5)
    svc.ingest("D1", d1)
    svc.ingest("W", w)
    return m5


def test_step_creates_one_signal_and_never_duplicates():
    svc, db = make_service()
    load(svc)
    msgs = svc.step()
    sig_msgs = [m for m in msgs if m.kind == "SIGNAL"]
    assert len(sig_msgs) == 1 and sig_msgs[0].chart
    assert "Entry Zone: 4100.0 - 4105.0" in sig_msgs[0].text
    assert len(db.active_signals()) == 1
    assert [m for m in svc.step() if m.kind == "SIGNAL"] == []  # повторный шаг без новых данных


def test_events_are_delivered_once_with_tz_texts():
    svc, db = make_service()
    m5 = load(svc)
    svc.step()
    bars = path(m5[-1].t + 300, m5[-1].c, [4103.0, 4098.0, 4101.5, 4120.0, 4123.0])
    svc.ingest("M5", bars)
    msgs = svc.step()
    kinds = [m.event for m in msgs if m.kind == "EVENT"]
    assert kinds[:3] == ["ZONE", "ENTRY", "TP1"]
    assert any("Цена в зоне, жди точку 3" in m.text for m in msgs)
    assert any("Условие входа выполнено" in m.text for m in msgs)
    assert any("TP1" in m.text and "п." in m.text for m in msgs)
    assert [m for m in svc.step() if m.kind == "EVENT"] == []  # без дублей


def test_daily_limit_blocks_extra_signals():
    svc, db = make_service(max_signals_day=0)
    load(svc)
    assert [m for m in svc.step() if m.kind == "SIGNAL"] == []
    assert db.active_signals() == []


def test_no_signal_without_confirmed_zone_only_watch():
    svc, db = make_service()
    m5 = load(svc)
    db.close()  # заменить БД: оставляем только историю до подтверждения зоны (до реакции на H1)
    svc, db = make_service()
    cut = next(i for i, c in enumerate(m5) if c.t >= m5[-1].t - 8 * 3600)
    svc.ingest("M5", m5[:cut])
    _, d1, w = build_buy()
    svc.ingest("D1", d1)
    svc.ingest("W", w)
    msgs = svc.step()
    assert not [m for m in msgs if m.kind == "SIGNAL"]
    assert len([m for m in msgs if m.kind == "WATCH"]) <= 1  # без завала «наблюдениями»


def test_webhook_signal_validation_and_event_flow():
    svc, db = make_service(mode="A")
    payload = {
        "type": "signal", "id": "T1", "side": "BUY", "strength": "A", "time": 1_700_000_000,
        "zone_lo": 4100.0, "zone_hi": 4104.5, "sl": 4094.7, "tps": [4118, 4131, 4146],
        "confirmations": [[True, "Bias H4/D1: бычий поток"], [False, "Вход M5: жди точку 3"]],
        "tfs": ["D1", "H4", "H1", "M15", "M5"], "feed": "OANDA",
    }
    msgs = svc.handle_webhook(payload)
    assert len(msgs) == 1 and "TP1: 4118.0  (+15.8 п.)  RR 1:2.1" in msgs[0].text
    assert svc.handle_webhook(payload) == []  # повтор того же id
    for name, snippet in (("ZONE", "Цена в зоне"), ("ENTRY", "Условие входа"), ("TP1", "TP1 +15.8")):
        out = svc.handle_webhook({"type": "event", "id": "T1", "event": name, "time": 1_700_000_300, "price": 4101.0})
        assert len(out) == 1 and snippet in out[0].text
    assert svc.handle_webhook({"type": "event", "id": "T1", "event": "TP1"}) == []  # дубль события
    out = svc.handle_webhook({"type": "event", "id": "T1", "event": "SL", "time": 1_700_000_900})
    assert "Стоп сработал после TP1" in out[0].text


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"zone_hi": 4115.0}, "wide"),
        ({"sl": 4070.0}, "sl_far"),
        ({"tps": [4105.0]}, "rr"),
        ({"strength": "C"}, "class_c"),
        ({"tps": []}, "no_target"),
    ],
)
def test_webhook_rejects_bad_signals(changes, reason):
    svc, db = make_service(mode="A")
    payload = {
        "type": "signal", "id": "X", "side": "BUY", "strength": "A", "time": 1_700_000_000,
        "zone_lo": 4100.0, "zone_hi": 4104.5, "sl": 4094.7, "tps": [4118, 4131], **changes,
    }
    assert svc.handle_webhook(payload) == []
    stored = db.get_by_key("X")
    assert stored.kind == "SKIPPED" and stored.reason == reason


def test_webhook_unknown_type_and_time_parsing():
    svc, _ = make_service(mode="A")
    with pytest.raises(ValueError):
        svc.handle_webhook({"type": "boom"})
    assert parse_time(1_700_000_000_000) == 1_700_000_000
    assert parse_time("2023-11-14T22:13:20Z") == 1_700_000_000
    assert parse_time("1700000000") == 1_700_000_000


def test_old_zones_do_not_flood_watch_on_cold_start():
    from xau_engine.service import WATCH_MAX_AGE_S

    svc, db = make_service()
    load(svc)
    a = svc.current_analysis()
    for c in a.candidates:  # сдвигаем «сейчас» на неделю вперёд: все зоны устарели
        c.created_t += 7 * 86400
        c.status = "WATCH"
    assert svc.register(a.candidates) == []
    assert WATCH_MAX_AGE_S == 48 * 3600
