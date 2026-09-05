"""Precompute and persist forecasts for the whole universe.

Per-ticker failures are logged and never make a completed vintage disappear.
The dashboard reads this store and never trains models inside a web request.

P0 also computes a diagnostic direct-horizon alpha forecast.  It remains
non-primary until its own walk-forward kill criteria pass.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.forecast_service import compute_and_save


def main() -> int:
    # A connection is never held across training: each forecast burns CPU, and
    # a connection left idle-in-transaction that long is killed by the server,
    # taking every subsequent ticker down with it.
    with db.connect() as conn:
        tickers = [c["ticker"] for c in db.list_companies(conn)]

    failures = 0
    for t in tickers:
        try:
            r = compute_and_save(t)
            if r.get("status") != "OK":
                failures += 1
                print(json.dumps({"ticker": t, "status": r.get("status"), "reason": r.get("reason")}))
                continue
            alpha = r.get("alpha_forecast") or {}

            h12 = r["models"][r["primary_model"]]["horizons"]["12m"]
            print(json.dumps({
                "ticker": t,
                "primary": r["primary_model"],
                "p50_12m": h12["p50"],
                "prob_up_12m": h12["prob_positive"],
                "lstm_beats": r["validation"]["verdict"]["lstm_beats_baseline"],
                "alpha_status": alpha.get("status"),
                "alpha_all_horizons_pass": (
                    alpha.get("promotion", {}).get("passed_all_horizons")
                ),
            }))
        except Exception as e:
            failures += 1
            print(json.dumps({"ticker": t, "status": "error",
                              "error": f"{type(e).__name__}: {e}"}))

    print(f"done, failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
