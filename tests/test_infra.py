import asyncio
import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from scenario import P
from test_service import load, make_service
from xau_engine.bot import keyboards as kb
from xau_engine.bot.delivery import Delivery
from xau_engine.bot.handlers import build_router
from xau_engine.config import Settings, load_settings
from xau_engine.db import Database
from xau_engine.models import Candle
from xau_engine.server import create_app
from xau_engine.timeutil import in_quiet_hours, trading_day_end, to_bishkek

SECRET = "s3cret"


def alert(**kw):
    base = {"type": "signal", "id": "W1", "side": "BUY", "strength": "A", "time": 1_700_000_000,
            "zone_lo": 4100.0, "zone_hi": 4104.5, "sl": 4094.7, "tps": [4118, 4131, 4146], "feed": "OANDA",
            "confirmations": [[True, "Bias H4/D1: бычий поток"]], "tfs": ["D1", "H4", "M5"]}
    base.update(kw)
    return base


@pytest.fixture
def stack():
    cfg = Settings(mode="A", params=P, webhook_secret=SECRET, dry_run=True)
    db = Database(":memory:")
    from xau_engine.service import Service

    svc = Service(db, cfg)
    delivery = Delivery(None, db, svc, cfg)
    db.add_user(1, "u1", 0)
    app = create_app(cfg, db, svc, delivery)
    return TestClient(app), db, svc, delivery


# ------------------------------------------------------------------ вебхук
def test_webhook_requires_secret_and_json(stack):
    c, *_ = stack
    assert c.post("/webhook/tradingview", json=alert()).status_code == 403
    assert c.post("/webhook/tradingview", json={**alert(), "secret": "bad"}).status_code == 403
    assert c.post("/webhook/tradingview", content="не json", headers={"content-type": "text/plain"}).status_code == 400
    assert c.post("/webhook/tradingview", json={**alert(), "secret": SECRET, "type": "boom"}).status_code == 400
    assert c.get("/health").json()["ok"] is True


def test_webhook_secret_via_query_and_text_plain_body(stack):
    # TradingView шлёт тело как text/plain
    c, db, _, delivery = stack
    r = c.post(f"/webhook/tradingview?secret={SECRET}", content=json.dumps(alert()),
               headers={"content-type": "text/plain"})
    assert r.status_code == 200 and r.json()["messages"] == 1
    assert delivery.outbox and "Entry Zone: 4100.0 - 4104.5" in delivery.outbox[0][1]


def test_webhook_disabled_without_secret():
    cfg = Settings(mode="A", params=P, webhook_secret="", dry_run=True)
    db = Database(":memory:")
    from xau_engine.service import Service

    svc = Service(db, cfg)
    c = TestClient(create_app(cfg, db, svc, Delivery(None, db, svc, cfg)))
    assert c.post("/webhook/tradingview", json=alert()).status_code == 503


def test_webhook_event_chain_notifies_only_signal_recipients(stack):
    c, db, _, delivery = stack
    db.add_user(2, "u2", 0)
    db.update_user_settings(2, zone_max=5, min_rr=3.0)  # u2 фильтрует: RR1 здесь 2.1 -> сигнал не получит
    c.post("/webhook/tradingview", json={**alert(), "secret": SECRET})
    for e in ("ZONE", "ENTRY", "TP1"):
        c.post("/webhook/tradingview", json={"secret": SECRET, "type": "event", "id": "W1", "event": e,
                                             "time": 1_700_000_100, "price": 4102})
    chats = [x[0] for x in delivery.outbox]
    assert chats.count(1) == 4 and 2 not in chats


def test_mode_b_ignores_signal_alerts():
    cfg = Settings(mode="B", params=P, webhook_secret=SECRET, dry_run=True)
    db = Database(":memory:")
    from xau_engine.service import Service

    svc = Service(db, cfg)
    c = TestClient(create_app(cfg, db, svc, Delivery(None, db, svc, cfg)))
    r = c.post("/webhook/tradingview", json={**alert(), "secret": SECRET})
    assert r.status_code == 200 and "ignored" in r.json()


# ------------------------------------------------------------------ доставка
def test_user_filters_for_width_rr_watch_and_quiet(stack):
    _, db, svc, delivery = stack
    db.add_user(3, "narrow", 0)
    db.update_user_settings(3, zone_max=5)
    db.add_user(4, "norr", 0)
    db.update_user_settings(4, min_rr=None, send_watch=False)
    wide = alert(id="W2", zone_hi=4108.0, tps=[4130, 4140], sl=4092.0)  # ширина 8
    msgs = svc.handle_webhook(wide)
    assert msgs
    asyncio.run(delivery.send(msgs))
    got = {c for c, *_ in delivery.outbox}
    assert 3 not in got and {1, 4} <= got
    delivery.outbox.clear()
    asyncio.run(delivery.send(svc.handle_webhook(alert(id="W3", type="watch", zone_lo=4200, zone_hi=4205))))
    assert {c for c, *_ in delivery.outbox} == {1, 3}  # u4 отключил «на наблюдении»


def test_quiet_hours_logic_with_midnight_wrap():
    ts = lambda h: int(__import__("datetime").datetime(2026, 1, 5, h, 30, tzinfo=to_bishkek(0).tzinfo).timestamp())
    assert in_quiet_hours(ts(3), 0, 8) and not in_quiet_hours(ts(9), 0, 8)
    assert in_quiet_hours(ts(23), 22, 6) and in_quiet_hours(ts(2), 22, 6) and not in_quiet_hours(ts(12), 22, 6)
    assert not in_quiet_hours(ts(3), 5, 5)


def test_trading_day_end_is_2300_bishkek():
    import datetime as dt

    tz = to_bishkek(0).tzinfo
    t = int(dt.datetime(2026, 10, 21, 14, 0, tzinfo=tz).timestamp())
    end = to_bishkek(trading_day_end(t))
    assert (end.day, end.hour) == (21, 23)
    late = int(dt.datetime(2026, 10, 21, 22, 50, tzinfo=tz).timestamp())
    assert to_bishkek(trading_day_end(late)).day == 22  # меньше 30 минут до конца — берём следующий день


# ------------------------------------------------------------------ обработчики бота
@dataclass
class FakeUser:
    id: int
    username: str = "tester"


@dataclass
class FakeChat:
    id: int


class FakeMsg:
    def __init__(self, text="", chat=1, user=1):
        self.text, self.chat, self.from_user = text, FakeChat(chat), FakeUser(user)
        self.sent: list[tuple[str, dict]] = []

    async def answer(self, text, **kw):
        self.sent.append((text, kw))


def handler(router, name):
    for h in router.message.handlers + router.callback_query.handlers:
        if h.callback.__name__ == name:
            return h.callback
    raise KeyError(name)


def make_bot(**cfg_kw):
    cfg = Settings(mode="A", params=P, dry_run=True, **cfg_kw)
    db = Database(":memory:")
    from xau_engine.service import Service

    svc = Service(db, cfg)
    return build_router(db, svc, cfg), db, svc, cfg


def test_start_subscribes_and_shows_menu():
    r, db, svc, cfg = make_bot()
    m = FakeMsg("/start")
    asyncio.run(handler(r, "start")(m))
    assert 1 in db.subscribers()
    assert "10 пунктов" in m.sent[0][0] and m.sent[0][1]["reply_markup"] is not None
    labels = [b.text for row in m.sent[0][1]["reply_markup"].keyboard for b in row]
    assert labels == [kb.BTN_ACTIVE, kb.BTN_HISTORY, kb.BTN_ANALYSIS, kb.BTN_SETTINGS, kb.BTN_TV]


def test_private_bot_rejects_strangers():
    r, db, *_ = make_bot(allowed_user_ids=frozenset({42}))
    m = FakeMsg("/start", chat=7, user=7)
    asyncio.run(handler(r, "start")(m))
    assert 7 not in db.subscribers() and "Нет доступа" in m.sent[0][0]


def test_buttons_require_start_then_work():
    r, db, svc, cfg = make_bot()
    m = FakeMsg(kb.BTN_ACTIVE)
    asyncio.run(handler(r, "active")(m))
    assert "Сначала /start" in m.sent[0][0]
    asyncio.run(handler(r, "start")(FakeMsg()))
    for name, snippet in (("active", "Активных сигналов нет"), ("history", "История"), ("settings", "Настройки")):
        m = FakeMsg()
        asyncio.run(handler(r, name)(m))
        assert snippet in m.sent[0][0]
    m = FakeMsg()
    asyncio.run(handler(r, "tv")(m))
    assert "tradingview.com" in m.sent[0][1]["reply_markup"].inline_keyboard[0][0].url


def test_analysis_button_rate_limited_to_once_per_5_minutes():
    r, db, svc, cfg = make_bot()
    asyncio.run(handler(r, "start")(FakeMsg()))
    m1, m2 = FakeMsg(), FakeMsg()
    asyncio.run(handler(r, "analysis")(m1))
    asyncio.run(handler(r, "analysis")(m2))
    assert "режиме A" in m1.sent[0][0]  # в режиме A у сервера нет свечей для разового разбора
    assert "раз в 5 мин" in m2.sent[0][0]


def test_settings_callbacks_toggle():
    r, db, svc, cfg = make_bot()
    asyncio.run(handler(r, "start")(FakeMsg()))

    class CB:
        def __init__(self, data):
            self.data = data
            self.message = type("M", (), {"chat": FakeChat(1), "edit_text": self.edit})()
            self.edited = None

        async def edit(self, text, **kw):
            self.edited = text

        async def answer(self, *a, **kw):
            pass

    cb = handler(r, "set_cb")
    asyncio.run(cb(CB("set:width")))
    assert db.user_settings(1)["zone_max"] == 5
    for _ in range(4):
        asyncio.run(cb(CB("set:rr")))
    assert db.user_settings(1)["min_rr"] == 2.0  # полный круг: 2 -> 3 -> выкл -> 1.5 -> 2
    asyncio.run(cb(CB("set:watch")))
    asyncio.run(cb(CB("set:quiet")))
    s = db.user_settings(1)
    assert s["send_watch"] is False and s["quiet"] is False


# ------------------------------------------------------------------ БД
def test_db_candles_signals_events_roundtrip():
    db = Database(":memory:")
    db.upsert_candles("M5", [Candle(300 * i, 1, 2, 0.5, 1.5) for i in range(10)])
    db.upsert_candles("M5", [Candle(300 * 9, 1, 3, 0.5, 2.5)])  # апдейт той же свечи
    got = db.load_candles("M5", 5)
    assert len(got) == 5 and got[-1].h == 3 and db.last_candle_t("M5") == 2700
    db.prune_candles("M5", 3)
    assert len(db.load_candles("M5", 100)) == 3
    assert db.add_event(1, "ZONE", 1, None) and not db.add_event(1, "ZONE", 2, None)


def test_db_survives_restart(tmp_path):
    path = str(tmp_path / "x.sqlite3")
    svc, db = make_service()
    # перенос: сохраняем сигнал в файловую БД, закрываем, открываем заново
    from xau_engine.service import Service

    cfg = Settings(mode="B", params=P)
    fdb = Database(path)
    s = Service(fdb, cfg)
    load(s)
    assert [m for m in s.step() if m.kind == "SIGNAL"]
    fdb.close()
    again = Database(path)
    assert len(again.active_signals()) == 1 and len(again.load_candles("M5", 10)) == 10


# ------------------------------------------------------------------ настройки
def test_settings_from_env_and_defaults(monkeypatch, tmp_path):
    import os

    monkeypatch.setattr(os, "environ", os.environ.copy())  # .env пишет в os.environ: не протекаем в другие тесты
    for k in ("POINT_SIZE", "ZONE_MAX", "MIN_RR", "MODE", "MAX_SIGNALS_DAY", "ALLOWED_USER_IDS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("DAY_OPEN_UTC_HOUR", raising=False)
    cfg = load_settings(None)
    assert cfg.mode == "B" and cfg.params.day_open_utc_hour == 0  # основной режим сейчас — B (Twelve Data)
    monkeypatch.setenv("MODE", "A")
    assert load_settings(None).params.day_open_utc_hour == 21  # OANDA
    monkeypatch.delenv("MODE")
    assert (cfg.params.point_size, cfg.params.zone_target, cfg.params.zone_max) == (1.0, 5.0, 10.0)
    assert (cfg.params.sl_buffer, cfg.params.sl_max, cfg.params.min_rr, cfg.max_signals_day) == (1.5, 20.0, 2.0, 3)
    assert (cfg.params.doji_body_ratio, cfg.params.perfect_double_tol, cfg.params.big_imbalance) == (0.10, 0.5, 3.0)
    assert cfg.params.big_wick_ratio == 0.5 and (cfg.quiet_start, cfg.quiet_end) == (0, 8)
    env = tmp_path / ".env"
    env.write_text("POINT_SIZE=0.1\nMIN_RR=off\nMODE=b\nALLOWED_USER_IDS=1, 2\nMAX_SIGNALS_DAY=2\n")
    cfg = load_settings(env)
    assert cfg.params.point_size == 0.1 and cfg.params.min_rr is None and cfg.mode == "B"
    assert cfg.allowed_user_ids == {1, 2} and cfg.max_signals_day == 2
    assert cfg.params.day_open_utc_hour == 0  # режим B: сутки Twelve Data по UTC
    monkeypatch.setenv("MODE", "X")
    with pytest.raises(ValueError):
        load_settings(None)
