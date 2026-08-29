"""Fetch and persist supported macro series for P1 regime research."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.macro import SERIES, fetch_fred_csv, upsert_series


def main() -> int:
    summary = {}
    failures = 0
    for series_id in SERIES:
        try:
            rows = fetch_fred_csv(series_id)
            with db.connect() as conn:
                n = upsert_series(conn, rows)
            summary[series_id] = {"status": "ok", "rows": n}
        except Exception as exc:
            failures += 1
            summary[series_id] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
