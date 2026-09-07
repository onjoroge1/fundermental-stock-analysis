"""Collect current estimates and verify precise history without fabricating backfills."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stock_machine import db
from stock_machine.ingestion.estimates import fetch_estimates
from stock_machine.prediction_inputs import fetch_consensus_history
from stock_machine.historical_coverage import inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tickers', default='AAPL,HIMS')
    args = parser.parse_args()
    results = []
    for ticker in args.tickers.upper().split(','):
        ticker = ticker.strip()
        if not ticker:
            continue
        payload = fetch_estimates(ticker)
        snapshots = payload['snapshots']
        with db.connect() as conn:
            db.insert_consensus_snapshots(conn, ticker, datetime.now(timezone.utc).date().isoformat(), snapshots)
            loaded = fetch_consensus_history(conn, ticker)
        identities = {(r['source'],r['available_at'],r['period_type'],r['forecast_period_end']) for r in loaded if r.get('available_at')}
        persisted = sum((r['source'],r['observed_at'],r['period_type'],r['forecast_period_end']) in identities for r in snapshots)
        errors = [e for e in payload['events'] if e['event'] != 'DATASET_LIMITATION']
        results.append({'ticker':ticker,'fetched':len(snapshots),'verified':persisted,
                        'status':'VERIFIED' if snapshots and persisted==len(snapshots) else 'NO_DATA' if not snapshots else 'FAILED',
                        'provider_events':errors})
    with db.connect() as conn:
        report = {'collection':results,'input_inventory':inventory(conn)}
    print(json.dumps(report,indent=2))
    # A source gap is explicit in the report. Persistence failure is a job failure.
    return int(any(r['status']=='FAILED' for r in results) or not any(r['status']=='VERIFIED' for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
