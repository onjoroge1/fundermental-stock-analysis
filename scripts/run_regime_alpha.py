"""Build the real PIT panel and evaluate the P1 regime challenger."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.backtest.engine import CAVEATS, run
from stock_machine.backtest.regime_panel import enrich
from stock_machine.backtest.regime_shadow import evaluate


def main() -> int:
    start = sys.argv[1] if len(sys.argv) > 1 else "2014-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else None
    with db.connect() as conn:
        observations, grid = run(conn, start=start, end=end)
        enriched, proxy_coverage = enrich(conn, observations)

    result = evaluate(enriched)
    result["panel"] = {
        "requested_start": start,
        "requested_end": end,
        "grid_dates": len(grid),
        "caveats": CAVEATS,
        "proxy_coverage": proxy_coverage,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    # REJECT/PENDING are valid research outcomes; process failure is reserved
    # for execution errors, not for a model failing its kill criterion.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
