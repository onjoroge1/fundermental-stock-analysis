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
from stock_machine.alpha_forecast import forecast_alpha
from stock_machine.prediction import forecast
from stock_machine.prediction_inputs import (
    fetch_consensus_history,
    fetch_surprise_history,
)


BENCHMARK_TICKER = os.getenv("PREDICTION_BENCHMARK", "SPY").upper()


def _price_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "date": r["date"],
            "close": r.get("close"),
            "adj_close": r.get("adj_close") or r.get("close"),
        }
        for r in rows
        if r.get("adj_close") is not None or r.get("close") is not None
    ]


def main() -> int:
    # A connection is never held across training: each forecast burns CPU, and
    # a connection left idle-in-transaction that long is killed by the server,
    # taking every subsequent ticker down with it.
    with db.connect() as conn:
        tickers = [c["ticker"] for c in db.list_companies(conn)]
        benchmark_rows = _price_rows(db.fetch_prices(conn, BENCHMARK_TICKER))

    failures = 0
    for t in tickers:
        try:
            with db.connect() as conn:
                rows = db.fetch_prices(conn, t)
                versions = db.latest_dataset_snapshots(conn, t)
                consensus_history = fetch_consensus_history(conn, t)
                surprises = fetch_surprise_history(conn, t)

            closes = _price_rows(rows)
            r = forecast(t, closes)          # no connection held here
            if r["status"] != "OK":
                print(json.dumps({"ticker": t, "status": r["status"],
                                  "reason": r.get("reason")}))
                continue

            if t == BENCHMARK_TICKER:
                alpha = {
                    "status": "NOT_APPLICABLE",
                    "benchmark": BENCHMARK_TICKER,
                    "reason": "benchmark ticker is not forecast against itself",
                }
            elif not benchmark_rows:
                alpha = {
                    "status": "PENDING_BENCHMARK_DATA",
                    "benchmark": BENCHMARK_TICKER,
                    "reason": f"no stored price history for {BENCHMARK_TICKER}",
                }
            else:
                alpha = forecast_alpha(
                    t,
                    closes,
                    BENCHMARK_TICKER,
                    benchmark_rows,
                    consensus_history=consensus_history,
                    surprises=surprises,
                )

            # Additive contract: existing consumers continue to use
            # primary_model/models/forecast_distribution unchanged.
            r["alpha_forecast"] = alpha
            r["input_data_versions"] = {
                v["dataset"]: v["content_hash"] for v in versions
                if v["dataset"] in {"prices", "consensus_estimates"}
            }
            r["alpha_input_coverage"] = {
                "benchmark": BENCHMARK_TICKER,
                "benchmark_price_rows": len(benchmark_rows),
                "consensus_vintages": len({x.get("snapshot_date")
                                            for x in consensus_history
                                            if x.get("snapshot_date")}),
                "earnings_surprises": len(surprises),
            }

            with db.connect() as conn:
                db.save_prediction_forecast(conn, r)

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
