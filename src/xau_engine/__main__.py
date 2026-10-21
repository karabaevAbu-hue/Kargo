"""CLI: run | backtest | fetch-history | test-alert."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from .config import load_settings


def cmd_run(args) -> None:
    import uvicorn

    from .app import build_app

    cfg = load_settings(args.env)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app, *_ = build_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


def cmd_backtest(args) -> None:
    from .backtest import derive_htf, format_report, load_csv, run_backtest, synthetic_m5

    cfg = load_settings(args.env)
    if args.synthetic:
        m5 = synthetic_m5(args.days, seed=args.seed)
        print("ВНИМАНИЕ: синтетические данные, оценка стратегии на них невозможна.", file=sys.stderr)
    else:
        if not args.m5:
            sys.exit("Укажи --m5 файл.csv (или --synthetic). Историю можно скачать командой fetch-history.")
        m5 = load_csv(args.m5)
    if args.d1 and args.w:
        d1, w = load_csv(args.d1), load_csv(args.w)
    else:
        d1, w = derive_htf(m5, cfg.params.day_open_utc_hour)
        print("D1/W собраны из M5 (приближение).", file=sys.stderr)
    if args.last_days:
        cut = m5[-1].t - args.last_days * 86400
        m5 = [c for c in m5 if c.t >= cut - args.warmup * 300]
    t0 = time.time()
    rep = run_backtest(m5, d1, w, cfg, warmup=args.warmup, stride=args.stride,
                       progress=lambda i, n: print(f"  {i}/{n}", file=sys.stderr))
    print(format_report(rep, cfg))
    print(f"(расчёт {time.time() - t0:.0f} с)", file=sys.stderr)
    if args.json:
        Path(args.json).write_text(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_fetch(args) -> None:
    from .backtest import save_csv
    from .feed.twelvedata import TwelveDataClient

    cfg = load_settings(args.env)

    async def go() -> None:
        c = TwelveDataClient(cfg.twelvedata_api_key, cfg.td_symbol)
        since = int(time.time()) - args.days * 86400
        out = Path(args.out)
        for tf, name in (("M5", "m5"), ("D1", "d1"), ("W", "w")):
            span = since if tf == "M5" else since - 400 * 86400
            data = await c.history(tf, span)
            save_csv(out / f"{name}.csv", data)
            print(f"{tf}: {len(data)} свечей -> {out / (name + '.csv')}")
        await c.aclose()

    asyncio.run(go())


def cmd_test_alert(args) -> None:
    """Шлёт на вебхук пробный сигнал и цепочку событий, как это делал бы индикатор."""
    import httpx

    now = int(time.time())
    sid = f"TEST:{now}"
    base = {"secret": args.secret}
    side = args.side.upper()
    buy = side == "BUY"
    lo, hi = (4100.0, 4104.5) if buy else (4140.0, 4144.5)
    sl = 4094.7 if buy else 4150.0
    tps = [4118.0, 4131.0, 4146.0] if buy else [4122.0, 4109.0, 4094.0]
    sign = {"type": "signal", "id": sid, "side": side, "strength": "A", "time": now, "zone_lo": lo,
            "zone_hi": hi, "sl": sl, "tps": tps, "feed": "OANDA", "tfs": ["D1", "H4", "H1", "M15", "M5"],
            "ob_tf": "H1", "confirmations": [[True, "Bias H4/D1: пробный поток"], [True, "Пробный сигнал (test-alert)"],
                                              [False, "Вход M5: жди точку 3"]]}
    steps = [sign] + [{"type": "event", "id": sid, "event": e, "time": now + 60 * (i + 1),
                       "price": (lo + hi) / 2} for i, e in enumerate(("ZONE", "ENTRY", "TP1", "TP2"))]
    with httpx.Client(timeout=15) as c:
        for s in steps:
            r = c.post(args.url, json={**base, **s})
            print(s.get("event", s["type"]), r.status_code, r.text)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="xau-engine", description="XAU Engine Pro (Mr. Casino)")
    ap.add_argument("--env", default=".env", help="путь к .env")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="запустить сервер, бота и планировщик")

    b = sub.add_parser("backtest", help="прогон стратегии на истории")
    b.add_argument("--m5", help="CSV: time,open,high,low,close (UTC)")
    b.add_argument("--d1")
    b.add_argument("--w")
    b.add_argument("--synthetic", action="store_true")
    b.add_argument("--days", type=int, default=30, help="для --synthetic")
    b.add_argument("--seed", type=int, default=1)
    b.add_argument("--warmup", type=int, default=1500, help="свечей M5 на разгон")
    b.add_argument("--stride", type=int, default=1, help="анализ каждые N свечей M5")
    b.add_argument("--last-days", type=int, default=0, help="взять только последние N дней")
    b.add_argument("--json", help="сохранить отчёт в JSON")

    f = sub.add_parser("fetch-history", help="скачать историю Twelve Data в CSV")
    f.add_argument("--days", type=int, default=90)
    f.add_argument("--out", default="data")

    t = sub.add_parser("test-alert", help="отправить пробный алерт на вебхук")
    t.add_argument("--url", default="http://127.0.0.1:8080/webhook/tradingview")
    t.add_argument("--secret", required=True)
    t.add_argument("--side", default="BUY", choices=["BUY", "SELL", "buy", "sell"])

    args = ap.parse_args(argv)
    {"backtest": cmd_backtest, "fetch-history": cmd_fetch, "test-alert": cmd_test_alert}.get(
        args.cmd or "run", cmd_run)(args)


if __name__ == "__main__":
    main()
