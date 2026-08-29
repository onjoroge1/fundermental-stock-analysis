"""Build PIT panel, add regime + macro state, evaluate the P1-B challenger."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.backtest.engine import run
from stock_machine.backtest.regime_panel import enrich as enrich_regime
from stock_machine.backtest.macro_panel import enrich as enrich_macro
from stock_machine.backtest.macro_model import walk_forward


def main() -> int:
    start = sys.argv[1] if len(sys.argv) > 1 else "2014-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else None
    with db.connect() as conn:
        observations, grid = run(conn, start=start, end=end)
        regime_rows, regime_coverage = enrich_regime(conn, observations)
        macro_rows, macro_coverage = enrich_macro(conn, regime_rows)
    result = walk_forward(macro_rows)
    result["panel"] = {
        "requested_start": start,
        "requested_end": end,
        "grid_dates": len(grid),
        "regime_coverage": regime_coverage,
        "macro_coverage": macro_coverage,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
