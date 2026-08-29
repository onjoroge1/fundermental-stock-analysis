"""Print the latest persisted unified-alpha shadow decision."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.backtest.shadow import MODEL_ID
from stock_machine.backtest.shadow_store import latest


def main() -> int:
    with db.connect() as conn:
        row = latest(conn, MODEL_ID)
    if row is None:
        print(json.dumps({
            "status": "PENDING",
            "model_id": MODEL_ID,
            "reason": "no persisted shadow evaluation",
        }, indent=2))
        return 1

    result = row["result"]
    payload = {
        "status": "OK",
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "model_id": MODEL_ID,
        "expectations_coverage": (result.get("coverage") or {}).get("expectations_coverage"),
        "expectations_dates": (result.get("coverage") or {}).get("expectations_dates"),
        "model_beats_baseline": (result.get("model") or {}).get("verdict", {}).get("model_beats_baseline"),
        "promotion_decision": (result.get("promotion") or {}).get("decision"),
        "deployed_as_primary": (result.get("promotion") or {}).get("deployed_as_primary", False),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
