"""Materialise the coverage table to disk.

Building 53 bundles takes ~5 minutes; doing it inside a web request makes the
app feel broken. This precomputes the exact payload /api/companies serves and
writes it with an as-of stamp, so the UI is instant and always states how
fresh the snapshot is. Run from the daily refresh (or by hand after ingest).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine.config import DATA_DIR
from stock_machine.webapp import companies

SNAPSHOT = DATA_DIR / "coverage_snapshot.json"


def main() -> int:
    started = time.monotonic()
    rows = companies(persisted=False)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "build_seconds": round(time.monotonic() - started, 1),
        "count": len(rows),
        "rows": rows,
    }
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(payload))
    print(json.dumps({k: v for k, v in payload.items() if k != "rows"}))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
