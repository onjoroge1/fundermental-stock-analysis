"""Precompute and persist forecasts for the whole universe.

Per-ticker failures are logged and never make a completed vintage disappear.
The dashboard reads this store and never trains models inside a web request.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.prediction import forecast


def main() -> int:
    conn = db.connect()
    try:
        tickers = [c["ticker"] for c in db.list_companies(conn)]
        failures = 0
        for t in tickers:
            try:
                rows = db.fetch_prices(conn, t)
                closes = [{"date": r["date"],
                           "adj_close": r.get("adj_close") or r["close"]}
                          for r in rows]
                r = forecast(t, closes)
                if r["status"] != "OK":
                    print(json.dumps({"ticker": t, "status": r["status"],
                                      "reason": r.get("reason")}))
                    continue
                versions = db.latest_dataset_snapshots(conn, t)
                r["input_data_versions"] = {
                    v["dataset"]: v["content_hash"] for v in versions
                    if v["dataset"] == "prices"
                }
                db.save_prediction_forecast(conn, r)
                h12 = r["models"][r["primary_model"]]["horizons"]["12m"]
                print(json.dumps({
                    "ticker": t, "primary": r["primary_model"],
                    "p50_12m": h12["p50"], "prob_up_12m": h12["prob_positive"],
                    "lstm_beats": r["validation"]["verdict"]["lstm_beats_baseline"],
                }))
            except Exception as e:
                failures += 1
                print(json.dumps({"ticker": t, "status": "error",
                                  "error": f"{type(e).__name__}: {e}"}))
        print(f"done, failures: {failures}")
        return 1 if failures else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
