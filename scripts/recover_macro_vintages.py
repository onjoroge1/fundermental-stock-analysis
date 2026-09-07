"""Recover quarterly ALFRED snapshots and report verified archive coverage."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stock_machine import db
from stock_machine.backtest.engine import quarterly_grid
from stock_machine.macro import SERIES
from stock_machine.macro_archive import fetch_vintage, save_vintage


def fetch(task):
    sid, vintage = task
    # ICE limits public history to three years from April 2026 onward.
    # Keep this source gap explicit instead of failing every older request.
    retention_start = date.today() - timedelta(days=3 * 365 + 1)
    if sid == "BAMLH0A0HYM2" and date.fromisoformat(vintage) < retention_start:
        return task, [], "UNAVAILABLE_PROVIDER_RETENTION"
    for attempt in range(3):
        try:
            return task, fetch_vintage(sid, vintage), None
        except Exception as exc:
            if attempt == 2:
                return task, [], f"{type(exc).__name__}: {exc}"
            time.sleep(2 ** attempt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2014-01-01')
    parser.add_argument('--end', default=date.today().isoformat())
    args = parser.parse_args()
    tasks = [(sid, (date.fromisoformat(d) - timedelta(days=1)).isoformat())
             for d in quarterly_grid(args.start, args.end) for sid in SERIES]
    results = []
    # Network fetches are independent; all database writes use one connection.
    with ThreadPoolExecutor(max_workers=4) as pool, db.connect() as conn:
        for (sid, vintage), rows, error in pool.map(fetch, tasks):
            inserted = save_vintage(conn, rows) if rows else 0
            result = {'series': sid, 'vintage': vintage, 'rows': len(rows),
                      'inserted': inserted, 'error': error,
                      'latest_observation': rows[-1]['observation_date'] if rows else None}
            results.append(result)
            print(json.dumps(result), flush=True)
    summary = {'requests': len(results), 'failed_requests': sum(bool(r['error']) and r['error'] != 'UNAVAILABLE_PROVIDER_RETENTION' for r in results),
               'unavailable_provider_retention': sum(r['error'] == 'UNAVAILABLE_PROVIDER_RETENTION' for r in results),
               'empty_archives': sum(not r['rows'] and not r['error'] for r in results),
               'rows_inserted': sum(r['inserted'] for r in results), 'vintages': results}
    Path('macro-archive-recovery.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'vintages'}))
    return int(bool(summary['failed_requests']))


if __name__ == '__main__':
    raise SystemExit(main())
