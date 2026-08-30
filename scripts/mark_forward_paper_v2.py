"""Mark every frozen Forward Paper v2 cohort on the latest complete market date."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.forward_paper_v2 import build_mark, list_cohorts, save_mark, marks, status


def main() -> int:
    failures = []
    results = []
    with db.connect() as conn:
        cohorts = list_cohorts(conn)
        for cohort in cohorts:
            try:
                mark = build_mark(conn, cohort)
                save_mark(conn, mark)
                cohort_marks = marks(conn, cohort["cohort_id"])
                results.append({
                    "cohort_id": cohort["cohort_id"],
                    "market_date": mark["market_date"],
                    "net_return_pct": mark["net_return_pct"],
                    "excess_return_pct": mark["excess_return_pct"],
                    "incubation": status(cohort, cohort_marks),
                })
            except Exception as exc:
                failures.append({
                    "cohort_id": cohort["cohort_id"],
                    "error": f"{type(exc).__name__}: {exc}",
                })
    print(json.dumps({
        "status": "OK" if not failures else "PARTIAL",
        "cohorts": results, "failures": failures,
    }, indent=2, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
