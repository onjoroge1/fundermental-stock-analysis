"""Build the real PIT panel, evaluate Strategy Lab v2, and persist the run."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.backtest.engine import CAVEATS, run as build_panel
from stock_machine.strategy_lab_v2 import run as run_lab
from stock_machine.strategy_lab_v2_store import panel_hash, save


def main() -> int:
    cost_bps = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    conn = db.connect()
    try:
        panel, grid = build_panel(conn)
    finally:
        conn.close()

    result = run_lab(panel, cost_bps=cost_bps)
    result["source_panel"] = {
        "observations": len(panel),
        "grid_dates": len(grid),
        "caveats": CAVEATS,
    }
    phash = panel_hash(panel)
    with db.connect() as conn:
        run_id = save(conn, result, phash)
    print(json.dumps({
        "run_id": run_id,
        "status": result.get("status"),
        "panel_hash": phash,
        "source_observations": len(panel),
        "date_split": result.get("date_split"),
        "p2_current_policy": result.get("p2_current_policy"),
        "eligible": {
            mode: [name for name, row in data.get("strategies", {}).items()
                   if (row.get("promotion") or {}).get("status") == "ELIGIBLE_FOR_FORWARD_PAPER_REVIEW"]
            for mode, data in (result.get("modes") or {}).items()
        },
    }, indent=2, default=str))
    return 0 if result.get("status") in {"OK", "INSUFFICIENT_HISTORY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
