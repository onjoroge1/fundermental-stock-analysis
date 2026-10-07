"""Wrap existing daily ingestion in a clock gate and durable stage receipt."""

import json
import os
from stock_machine.scheduled_operations import run
from scripts import daily_refresh


def operation():
    code = daily_refresh.main()
    paths = sorted(daily_refresh.LOG_DIR.glob("*.json"))
    log = json.loads(paths[-1].read_text()) if paths else {}
    predictions = log.get("prediction_precompute") or {}
    benchmark = os.getenv("BENCHMARKS_OUTCOME", "not_checked")
    return {
        "status": "FAILED" if code or benchmark != "success" else "OK",
        "benchmark_status": benchmark,
        "exit_code": code,
        "ingested": log.get("tickers", 0),
        "ingestion_failures": log.get("failures", 0),
        "forecasts_refreshed": predictions.get("ok", 0),
        "forecast_failures": predictions.get("failed", 0),
    }


def main():
    result = run(
        "ingestion",
        workflow_cron=os.getenv("REQUESTED_CRON"),
        manual=os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch",
        operation=operation,
    )
    print(json.dumps(result))
    return 1 if result["status"] in ("FAILED", "ATTENTION") else 0


if __name__ == "__main__":
    raise SystemExit(main())
