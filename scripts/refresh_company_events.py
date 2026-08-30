"""Refresh point-in-time earnings/dividend/split event snapshots.

This worker is intentionally separate from the fundamental pipeline so a
calendar-provider outage cannot block SEC/price/fundamental refreshes.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.events.store import replace_daily_snapshot
from stock_machine.ingestion.company_events import fetch_company_events


def main() -> int:
    with db.connect() as conn:
        tickers = [row["ticker"] for row in db.list_companies(conn)]

    failures = 0
    summary = []
    for ticker in tickers:
        try:
            payload = fetch_company_events(ticker, as_of=date.today())
            with db.connect() as conn:
                replace_daily_snapshot(
                    conn,
                    ticker,
                    payload["observed_on"],
                    payload["source"],
                    payload["events"],
                    payload["coverage"],
                )
            row = {
                "ticker": ticker,
                "status": "ok",
                "events": len(payload["events"]),
                "coverage": {
                    item["event_type"]: item["coverage_status"]
                    for item in payload["coverage"]
                },
            }
        except Exception as exc:
            failures += 1
            row = {
                "ticker": ticker,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        summary.append(row)
        print(json.dumps(row, default=str))

    print(json.dumps({
        "status": "ok" if not failures else "partial",
        "tickers": len(tickers),
        "failures": failures,
    }))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
