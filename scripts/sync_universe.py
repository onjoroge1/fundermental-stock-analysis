"""Ingest any tickers declared in sectors.UNIVERSE but absent from the DB.

This is intentionally additive. Removing a ticker from UNIVERSE does not
delete historical data or reports; persistent deletion requires a separate,
explicit maintenance action.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.bundle import build_bundle, write_bundle
from stock_machine.pipeline import run as run_pipeline
from stock_machine.sectors import UNIVERSE


def main() -> int:
    conn = db.connect()
    try:
        existing = {row["ticker"].upper() for row in db.list_companies(conn)}
    finally:
        conn.close()

    missing = [ticker for ticker in UNIVERSE if ticker.upper() not in existing]
    print(json.dumps({"existing": len(existing), "missing": missing}))
    failures: list[dict] = []

    for ticker in missing:
        try:
            result = run_pipeline(ticker)
            bundle = build_bundle(ticker)
            write_bundle(bundle)
            print(json.dumps({
                "ticker": ticker,
                "status": "added",
                "sector": (bundle.get("company") or {}).get("sector"),
                "data_quality": (bundle.get("data_quality") or {}).get("status"),
                "pipeline": result,
            }, default=str))
        except Exception as exc:
            failures.append({
                "ticker": ticker,
                "error": f"{type(exc).__name__}: {exc}",
            })
            print(json.dumps({"ticker": ticker, "status": "error", **failures[-1]}))

    if failures:
        print(json.dumps({"status": "failed", "failures": failures}))
        return 1
    print(json.dumps({"status": "ok", "added": missing}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
