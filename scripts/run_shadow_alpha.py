"""Build the real point-in-time panel, evaluate unified alpha, persist result."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.backtest.engine import CAVEATS, run
from stock_machine.backtest.shadow import evaluate_shadow
from stock_machine.backtest.shadow_store import save


def main() -> int:
    start = sys.argv[1] if len(sys.argv) > 1 else "2014-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else None

    with db.connect() as conn:
        observations, grid = run(conn, start=start, end=end)

    result = evaluate_shadow(observations)
    result["panel"] = {
        "requested_start": start,
        "requested_end": end,
        "grid_dates": len(grid),
        "caveats": CAVEATS,
    }

    with db.connect() as conn:
        run_id = save(conn, result, observations)

    output = {
        "run_id": run_id,
        "model_id": result["model_id"],
        "observations": result["coverage"]["observations"],
        "tickers": result["coverage"]["tickers"],
        "expectations_coverage": result["coverage"]["expectations_coverage"],
        "expectations_dates": result["coverage"]["expectations_dates"],
        "model_status": result["model"].get("status"),
        "model_beats_baseline": result["model"].get("verdict", {}).get("model_beats_baseline"),
        "promotion_decision": result["promotion"]["decision"],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
