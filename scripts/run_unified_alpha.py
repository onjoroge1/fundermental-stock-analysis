"""Run the unified alpha model from an exported backtest observation panel.

The panel is expected to contain point-in-time observations with fundamental,
market, expectations and forward-return fields. This script is intentionally
read-only and diagnostic; it never changes production ranking signals.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from stock_machine.backtest.unified_model import walk_forward


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/run_unified_alpha.py <panel.json>")
        return 2
    path = Path(sys.argv[1])
    rows = json.loads(path.read_text())
    result = walk_forward(rows)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
