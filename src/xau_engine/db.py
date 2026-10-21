"""SQLite: свечи, сигналы, события, пользователи и настройки (раздел 2 ТЗ)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Sequence

from .models import Candle
from .signals import ACTIVE, Signal
from .timeutil import day_key, week_key

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    tf TEXT NOT NULL, t INTEGER NOT NULL,
    o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL,
    PRIMARY KEY (tf, t)
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    side TEXT NOT NULL,
    strength TEXT NOT NULL,
    day TEXT NOT NULL,
    week TEXT NOT NULL,
    created_t INTEGER NOT NULL,
    closed_t INTEGER,
    result TEXT,
    result_points REAL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_kind_status ON signals(kind, status);
CREATE INDEX IF NOT EXISTS idx_signals_day ON signals(day);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    t INTEGER NOT NULL,
    price REAL,
    payload TEXT,
    UNIQUE (signal_id, kind)
);
CREATE TABLE IF NOT EXISTS deliveries (
    signal_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
    PRIMARY KEY (signal_id, chat_id)
);
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    username TEXT,
    created_t INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    settings TEXT NOT NULL DEFAULT '{}',
    last_analysis_t INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""

DEFAULT_USER_SETTINGS = {
    "zone_max": 10,  # 5 или 10: сигналы шире не присылать
    "min_rr": 2.0,  # None — не фильтровать
    "send_watch": True,
    "quiet": True,  # тихие часы: сообщения приходят без звука
}


class Database:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ свечи
    def upsert_candles(self, tf: str, candles: Iterable[Candle]) -> int:
        rows = [(tf, c.t, c.o, c.h, c.l, c.c) for c in candles]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO candles(tf,t,o,h,l,c) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(tf,t) DO UPDATE SET o=excluded.o,h=excluded.h,l=excluded.l,c=excluded.c",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def load_candles(self, tf: str, limit: int = 5000, since: int | None = None) -> list[Candle]:
        with self._lock:
            if since is not None:
                cur = self._conn.execute(
                    "SELECT t,o,h,l,c FROM candles WHERE tf=? AND t>=? ORDER BY t", (tf, since)
                )
            else:
                cur = self._conn.execute(
                    "SELECT t,o,h,l,c FROM (SELECT t,o,h,l,c FROM candles WHERE tf=? ORDER BY t DESC LIMIT ?) "
                    "ORDER BY t",
                    (tf, limit),
                )
            return [Candle(*tuple(r)) for r in cur.fetchall()]

    def last_candle_t(self, tf: str) -> int | None:
        with self._lock:
            r = self._conn.execute("SELECT MAX(t) FROM candles WHERE tf=?", (tf,)).fetchone()
        return r[0]

    def prune_candles(self, tf: str, keep: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM candles WHERE tf=? AND t < (SELECT MIN(t) FROM "
                "(SELECT t FROM candles WHERE tf=? ORDER BY t DESC LIMIT ?))",
                (tf, tf, keep),
            )
            self._conn.commit()

    # ---------------------------------------------------------------- сигналы
    def insert_signal(self, sig: Signal) -> Signal:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO signals(key,kind,status,side,strength,day,week,created_t,closed_t,result,"
                "result_points,data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sig.key, sig.kind, sig.status, sig.side.value, sig.strength, day_key(sig.created_t),
                 week_key(sig.created_t), sig.created_t, sig.closed_t, sig.result, sig.result_points,
                 sig.to_json()),
            )
            self._conn.commit()
            sig.id = cur.lastrowid
        return sig

    def save_signal(self, sig: Signal) -> None:
        assert sig.id is not None
        with self._lock:
            self._conn.execute(
                "UPDATE signals SET kind=?,status=?,strength=?,closed_t=?,result=?,result_points=?,data=? "
                "WHERE id=?",
                (sig.kind, sig.status, sig.strength, sig.closed_t, sig.result, sig.result_points,
                 sig.to_json(), sig.id),
            )
            self._conn.commit()

    def _signals(self, where: str, args: Sequence = (), order: str = "created_t DESC", limit: int | None = None):
        q = f"SELECT id,data FROM signals WHERE {where} ORDER BY {order}"
        if limit:
            q += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [Signal.from_json(r["data"], r["id"]) for r in rows]

    def get_signal(self, id_: int) -> Signal | None:
        r = self._signals("id=?", (id_,))
        return r[0] if r else None

    def get_by_key(self, key: str) -> Signal | None:
        r = self._signals("key=?", (key,))
        return r[0] if r else None

    def active_signals(self) -> list[Signal]:
        marks = ",".join("?" for _ in ACTIVE)
        return self._signals(f"kind='SIGNAL' AND status IN ({marks})", ACTIVE, "created_t")

    def watch_signals(self, since: int = 0) -> list[Signal]:
        return self._signals("kind='WATCH' AND created_t>=?", (since,), "created_t DESC", 10)

    def count_watch_since(self, since: int) -> int:
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) FROM signals WHERE kind='WATCH' AND created_t>=?", (since,)
            ).fetchone()
        return r[0]

    def history(self, limit: int = 20) -> list[Signal]:
        """Последние сигналы (без наблюдений), включая пропущенные и активные."""
        return self._signals("kind IN ('SIGNAL','SKIPPED')", (), "created_t DESC", limit)

    def count_signals_day(self, day: str) -> int:
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) FROM signals WHERE kind='SIGNAL' AND day=?", (day,)
            ).fetchone()
        return r[0]

    def totals(self, field: str, value: str) -> dict:
        """Итоги закрытых сигналов за день/неделю: число, TP/SL, пункты."""
        assert field in ("day", "week")
        with self._lock:
            rows = self._conn.execute(
                f"SELECT result, result_points FROM signals WHERE kind='SIGNAL' AND {field}=? "
                "AND result_points IS NOT NULL",
                (value,),
            ).fetchall()
        pts = [r["result_points"] for r in rows]
        return {
            "n": len(rows),
            "wins": sum(1 for p in pts if p > 0),
            "losses": sum(1 for p in pts if p < 0),
            "points": sum(pts),
        }

    # ---------------------------------------------------------------- события
    def add_event(self, signal_id: int, kind: str, t: int, price: float | None, payload: dict | None = None) -> bool:
        """True, если событие новое (повторы игнорируются)."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO events(signal_id,kind,t,price,payload) VALUES (?,?,?,?,?)",
                (signal_id, kind, t, price, json.dumps(payload or {}, ensure_ascii=False)),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def events(self, signal_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind,t,price,payload FROM events WHERE signal_id=? ORDER BY t,id", (signal_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- доставки
    def add_delivery(self, signal_id: int, chat_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO deliveries(signal_id,chat_id) VALUES (?,?)", (signal_id, chat_id)
            )
            self._conn.commit()

    def delivered_chats(self, signal_id: int) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT chat_id FROM deliveries WHERE signal_id=?", (signal_id,)
            ).fetchall()
        return [r["chat_id"] for r in rows]

    # ---------------------------------------------------------- пользователи
    def add_user(self, chat_id: int, username: str | None, now: int) -> bool:
        """True, если пользователь новый или вернулся после отписки."""
        with self._lock:
            row = self._conn.execute("SELECT active FROM users WHERE chat_id=?", (chat_id,)).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO users(chat_id,username,created_t,active,settings) VALUES (?,?,?,1,?)",
                    (chat_id, username, now, json.dumps(DEFAULT_USER_SETTINGS)),
                )
                self._conn.commit()
                return True
            self._conn.execute("UPDATE users SET active=1, username=? WHERE chat_id=?", (username, chat_id))
            self._conn.commit()
            return not row["active"]

    def deactivate_user(self, chat_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET active=0 WHERE chat_id=?", (chat_id,))
            self._conn.commit()

    def user_settings(self, chat_id: int) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT settings FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        s = dict(DEFAULT_USER_SETTINGS)
        if row:
            s.update(json.loads(row["settings"]))
        return s

    def update_user_settings(self, chat_id: int, **changes) -> dict:
        s = self.user_settings(chat_id)
        s.update(changes)
        with self._lock:
            self._conn.execute("UPDATE users SET settings=? WHERE chat_id=?", (json.dumps(s), chat_id))
            self._conn.commit()
        return s

    def subscribers(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute("SELECT chat_id FROM users WHERE active=1").fetchall()
        return [r["chat_id"] for r in rows]

    def last_analysis(self, chat_id: int) -> int:
        with self._lock:
            row = self._conn.execute("SELECT last_analysis_t FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        return row["last_analysis_t"] if row else 0

    def set_last_analysis(self, chat_id: int, t: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE users SET last_analysis_t=? WHERE chat_id=?", (t, chat_id))
            self._conn.commit()

    # --------------------------------------------------------------------- kv
    def kv_get(self, k: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default

    def kv_set(self, k: str, v: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v)
            )
            self._conn.commit()
