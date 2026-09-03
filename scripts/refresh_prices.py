"""Refresh only stale daily price datasets.

Unlike daily_refresh.py this intentionally skips SEC/fundamentals, forecasts,
reports, bundles, and paper marking. It is safe to run immediately before a
Forward Paper mark.
"""
from __future__ import annotations

import argparse
import json

from stock_machine import db
from stock_machine.market_health import DEFAULT_MAX_AGE_HOURS, health, refresh_prices


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    with db.connect() as conn:
        requested = args.tickers or [c["ticker"] for c in db.list_companies(conn)]
        before = health(conn, max_age_hours=args.max_age_hours)
        result = refresh_prices(
            conn,
            requested,
            only_if_stale=not args.force,
            max_age_hours=args.max_age_hours,
        )
    print(json.dumps({"before": before, "refresh": result}, indent=2, default=str))
    return 0 if result["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
