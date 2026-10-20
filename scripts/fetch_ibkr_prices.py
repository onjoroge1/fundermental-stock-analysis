"""Fetch daily price bars from IBKR to local files, for the implied-volatility comparison.

  python scripts/fetch_ibkr_prices.py [--port 4001] [--tickers AAPL,SPY] [--out data/ibkr_prices]

Runs where TWS / IB Gateway is logged in (read-only API; no orders, no
credentials, no database). Two series per symbol, joined on date the way the
repo's broker ingestion does (stock_machine/ingestion/prices_tws.py):
TRADES (split-adjusted OHLC, volume) and ADJUSTED_LAST (split- and
dividend-adjusted close, used as adj_close). 15 years, paced to IBKR's limits;
resumable. Files stay local: IBKR's terms restrict redistributing its data.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("probe_ibkr_iv_history", ROOT / "scripts" / "probe_ibkr_iv_history.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

DURATION = "15 Y"
PROVENANCE = "IBKR TWS reqHistoricalData, 1-day bars: TRADES (OHLC, volume) joined with ADJUSTED_LAST (adj_close)"


def merge(trades: list[dict], adjusted: list[dict]) -> list[dict]:
    """Join on date; a date without an adjusted close keeps None (never the raw close)."""
    adj = {r["date"]: r["close"] for r in adjusted}
    return [{"date": r["date"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
             "adj_close": adj.get(r["date"]), "volume": r.get("volume", 0.0)} for r in trades]


def fetch_symbol(session, symbol: str, duration: str = DURATION) -> dict:
    trades, t_errors = session.daily_bars(symbol, "TRADES", duration)
    adjusted, a_errors = session.daily_bars(symbol, "ADJUSTED_LAST", duration)
    rows = merge(trades, adjusted)
    return {"ticker": symbol, "retrieved_at": datetime.now(timezone.utc).isoformat(), "provenance": PROVENANCE,
            "duration": duration, "rows": rows, "errors": [list(e) for e in t_errors + a_errors],
            "first": rows[0]["date"] if rows else None, "last": rows[-1]["date"] if rows else None,
            "missing_adj_close": sum(r["adj_close"] is None for r in rows)}


def run(session, symbols: list[str], out_dir: Path, *, force: bool = False, log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for k, symbol in enumerate(symbols, 1):
        path = out_dir / f"{symbol}.json"
        saved = json.loads(path.read_text()) if path.exists() and not force else None
        if saved and saved.get("rows") and not saved.get("errors"):
            data = saved
        else:
            data = fetch_symbol(session, symbol)
            path.write_text(json.dumps(data))
        summary[symbol] = {k2: data[k2] for k2 in ("first", "last", "missing_adj_close")} | {
            "bars": len(data["rows"]), "errors": data["errors"]}
        log(f"[{k}/{len(symbols)}] {symbol}: {summary[symbol]['bars']} bars from {data['first']}")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    return summary


def main() -> int:
    from stock_machine.agents.contracts import AGENT_UNIVERSE
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("IBKR_TWS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("IBKR_TWS_PORT", "4001")))
    parser.add_argument("--client-id", type=int, default=30)
    parser.add_argument("--tickers")
    parser.add_argument("--out", default="data/ibkr_prices")
    parser.add_argument("--spacing", type=float, default=11.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.host not in probe.LOCAL_HOSTS:
        raise SystemExit(f"refusing non-local host {args.host}")
    symbols = ([t.strip().upper() for t in args.tickers.split(",")] if args.tickers
               else sorted(set(AGENT_UNIVERSE) | {"SPY"}))
    out_dir = Path(args.out) / datetime.now().date().isoformat()
    try:
        session = probe.TWSSession(args.host, args.port, args.client_id, 60.0, args.spacing)
    except probe.ProbeError as exc:
        raise SystemExit(str(exc)) from None
    try:
        summary = run(session, symbols, out_dir, force=args.force)
    finally:
        session.close()
    short = {t: v for t, v in summary.items() if not v["bars"] or v["errors"]}
    print(f"wrote {out_dir}: {len(summary) - len(short)} of {len(summary)} symbols complete; incomplete: {sorted(short)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
