"""One forecast builder for command-line, scheduled and control-plane work."""
from __future__ import annotations

import os
from . import db
from .alpha_forecast import forecast_alpha, MODEL_VERSION as ALPHA_VERSION
from .prediction import forecast
from .prediction_inputs import fetch_consensus_history, fetch_surprise_history
from .data_quality import content_hash, readiness_for_snapshots
from .market_calendar import latest_completed_session

BUILDER_VERSION = "forecast-builder.v1"


def build_forecast(ticker: str, *, benchmark: str | None = None) -> dict:
    ticker = ticker.upper()
    benchmark = (benchmark or os.getenv("PREDICTION_BENCHMARK", "SPY")).upper()
    cutoff = latest_completed_session()
    # One database snapshot for every input; release it before training.
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        prices = [r for r in db.fetch_prices(conn, ticker) if r["date"] <= cutoff]
        benchmark_rows = [r for r in db.fetch_prices(conn, benchmark) if r["date"] <= cutoff]
        versions = db.latest_dataset_snapshots(conn, ticker)
        consensus = fetch_consensus_history(conn, ticker)
        surprises = fetch_surprise_history(conn, ticker)
    if prices and (prices[-1]["date"] != cutoff or any(not r.get("adj_close") for r in prices)):
        return {"status": "INVALID_OR_STALE_INPUT", "ticker": ticker,
                "reason": "forecast requires complete adjusted prices through the latest completed session",
                "expected_market_date": cutoff}
    result = forecast(ticker, prices)
    if result.get("status") != "OK":
        return result
    if ticker == benchmark:
        alpha = {"status": "NOT_APPLICABLE", "benchmark": benchmark}
    elif not benchmark_rows or benchmark_rows[-1]["date"] != cutoff:
        alpha = {"status": "PENDING_BENCHMARK_DATA", "benchmark": benchmark}
    else:
        try:
            alpha = forecast_alpha(ticker, prices, benchmark, benchmark_rows,
                                   consensus_history=consensus, surprises=surprises)
        except Exception as exc:
            # A failed component is explicit; it cannot disappear from a
            # payload depending on which worker happened to produce it.
            alpha = {"status": "FAILED", "benchmark": benchmark,
                     "reason": f"alpha computation failed: {type(exc).__name__}"}
    result["alpha_forecast"] = alpha
    result["builder_version"] = BUILDER_VERSION
    result["component_versions"] = {"alpha": ALPHA_VERSION}
    result["input_data_versions"] = {
        **{v["dataset"]: v["content_hash"] for v in versions},
        "prices": content_hash(prices),
        "benchmark_prices": content_hash(benchmark_rows),
        "consensus": content_hash(consensus),
        "earnings_surprises": content_hash(surprises),
    }
    result["data_quality"] = readiness_for_snapshots({v["dataset"]: v for v in versions})
    result["alpha_input_coverage"] = {
        "benchmark": benchmark, "benchmark_price_rows": len(benchmark_rows),
        "consensus_vintages": len({r["snapshot_date"] for r in consensus}),
        "earnings_surprises": len(surprises),
    }
    return result


def compute_and_save(ticker: str) -> dict:
    result = build_forecast(ticker)
    if result.get("status") == "OK":
        with db.connect() as conn:
            result["forecast_id"] = db.save_prediction_forecast(conn, result)
    return result
