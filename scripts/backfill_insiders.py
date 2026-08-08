"""One-time Form 4 backfill for every covered ticker, using the submissions
JSON already in immutable raw storage — no FMP calls, only SEC fetches for
Form 4 XMLs not yet in the database (incremental / resumable)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.ingestion.form4 import ingest_from_submissions
from stock_machine.provenance import latest_raw, load_raw


def main() -> int:
    conn = db.connect()
    try:
        db.init_schema(conn)
        companies = db.list_companies(conn)
        failures = 0
        for c in companies:
            t = c["ticker"]
            raw_path = latest_raw("sec", [t, "submissions"])
            if not raw_path:
                print(json.dumps({"ticker": t, "status": "no_submissions_raw"}))
                failures += 1
                continue
            submissions = load_raw(raw_path)["original_payload"]
            try:
                stats = ingest_from_submissions(conn, t, c["cik"], submissions)
                print(json.dumps({"ticker": t, **stats}))
            except Exception as e:
                failures += 1
                print(json.dumps({"ticker": t, "status": "error",
                                  "error": f"{type(e).__name__}: {e}"}))
        print(f"\ndone, failures: {failures}")
        return 1 if failures else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
