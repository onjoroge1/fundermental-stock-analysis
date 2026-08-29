"""Capture current option-implied surfaces from the configured market provider.

Designed to run where TWS/IB Gateway is reachable.  It is intentionally not a
cloud cron because a local broker session is normally required.  Each successful
run persists a point-in-time surface that later becomes historical training data.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.market_data import get_provider
from stock_machine.options.surface_features import extract_surface
from stock_machine.options.surface_store import history, save

MAX_EXPIRIES = int(os.getenv("P1_OPTION_EXPIRIES", "2"))
MAX_STRIKES = int(os.getenv("P1_OPTION_STRIKES", "18"))


def _spot(q) -> float | None:
    if q.mark is not None and q.mark > 0:
        return q.mark
    if q.bid is not None and q.ask is not None and q.ask >= q.bid:
        return (q.bid + q.ask) / 2
    return q.last


def main() -> int:
    with db.connect() as conn:
        universe = [c["ticker"] for c in db.list_companies(conn)]
    env = os.getenv("P1_OPTION_TICKERS", "").strip()
    tickers = [x.strip().upper() for x in env.split(",") if x.strip()] if env else universe

    provider = get_provider()
    failures = 0
    try:
        for ticker in tickers:
            try:
                underlying = provider.resolve_underlying(ticker)
                months = list(underlying.option_months or [])[:MAX_EXPIRIES]
                if not months:
                    raise RuntimeError("provider returned no option months")
                quote = provider.quote_underlying(ticker)
                spot = _spot(quote)
                if spot is None or spot <= 0:
                    raise RuntimeError("underlying quote has no usable spot price")

                chains = []
                for month in months:
                    strikes = provider.available_strikes(ticker, month)
                    ladder = sorted(set(strikes.call_strikes) | set(strikes.put_strikes),
                                    key=lambda x: abs(x - spot))[:MAX_STRIKES]
                    ladder = sorted(ladder)
                    if not ladder:
                        continue
                    chains.append(provider.option_chain(ticker, month, ladder))
                if not chains:
                    raise RuntimeError("no option chains captured")

                with db.connect() as conn:
                    prior = history(conn, ticker, before_as_of=max(c.fetched_at for c in chains).isoformat())
                surface = extract_surface(chains, prior_surfaces=prior)
                with db.connect() as conn:
                    snapshot_id = save(conn, surface)
                print(json.dumps({
                    "ticker": ticker,
                    "status": "ok",
                    "snapshot_id": snapshot_id,
                    "as_of": surface["as_of"],
                    "features": surface["features"],
                }))
            except Exception as exc:
                failures += 1
                print(json.dumps({"ticker": ticker, "status": "error",
                                  "error": f"{type(exc).__name__}: {exc}"}))
    finally:
        provider.close()
    print(json.dumps({"tickers": len(tickers), "failures": failures}))
    return 1 if failures == len(tickers) and tickers else 0


if __name__ == "__main__":
    raise SystemExit(main())
