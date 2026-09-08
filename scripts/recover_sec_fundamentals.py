"""Repair targeted SEC inputs and verify accounting, share counts and forecasts."""
import json
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stock_machine import db
from stock_machine.accounting_quality import build_report
from stock_machine.config import ensure_dirs
from stock_machine.data_quality import assess_dataset
from stock_machine.forecast_service import compute_and_save
from stock_machine.ingestion import sec
from stock_machine.ingestion.cover_shares import supplement_companyfacts
from stock_machine.normalization.financial_periods import build_periods, extract_shares_outstanding
from stock_machine.normalization.earnings_releases import reviewed_eps
from stock_machine.provenance import save_raw

COVER_TARGETS = ('ABNB', 'DELL', 'GOOGL', 'HIMS', 'META', 'PLTR', 'RIVN')
LIABILITY_TARGETS = ('AAL', 'ADI', 'AMZN', 'DAL', 'DIS', 'ORCL', 'TGT', 'TMUS', 'UAL', 'VZ')
EPS_TARGETS = tuple(r['ticker'] for r in reviewed_eps())
TARGETS = tuple(dict.fromkeys(('UBER',) + COVER_TARGETS + LIABILITY_TARGETS + EPS_TARGETS))
LATEST_LIABILITY_TARGETS = ('AAL', 'ADI', 'DAL', 'ORCL', 'TGT', 'TMUS', 'UAL', 'VZ')


def main():
    ensure_dirs()
    with db.connect() as conn:
        before = build_report(conn)
        companies = {c['ticker']: c for c in db.list_companies(conn)}
    print(json.dumps({'before': {k: v for k, v in before.items() if k not in ('failures', 'latest_quarters')}}), flush=True)
    # Fetch and validate the complete target batch before replacing any periods.
    for r in reviewed_eps():
        content = sec._get(r['source_url']).text
        if hashlib.sha256(content.encode()).hexdigest() != r['source_sha256']:
            raise ValueError(f"{r['ticker']}: reviewed earnings exhibit changed; review required")
        save_raw('sec', [r['ticker'], 'earnings_release'], content, r['source_url'])
    prepared = []
    for ticker in TARGETS:
        cik = str(companies[ticker]['cik']).zfill(10)
        sub = sec.fetch_submissions(ticker, cik)
        facts = sec.fetch_companyfacts(ticker, cik)
        cover_events = supplement_companyfacts(ticker, cik, sub, facts)
        q, a, events = build_periods(facts)
        for r in reviewed_eps():
            if ticker == r['ticker']:
                period = next((p for p in q if p['period_end'] == r['period_end']), {})
                if (period.get('fields', {}).get(r['field']) != r['value']
                        or period.get('field_sources', {}).get(r['field']) != r['accession']):
                    raise ValueError(f'{ticker}: EPS recovery verification failed before writes')
        shares = extract_shares_outstanding(facts)
        if not q or not a or (ticker in COVER_TARGETS and not shares):
            raise ValueError(f'{ticker}: incomplete SEC recovery; no target periods have been replaced')
        prepared.append((ticker, q, a, shares, events + cover_events))
        print(json.dumps({'ticker': ticker, 'quarters': len(q), 'annuals': len(a),
                          'share_observations': len(shares), 'cover_events': cover_events}), flush=True)
    for ticker, q, a, shares, events in prepared:
        with db.connect() as conn:
            db.replace_periods(conn, ticker, q, a)
            persisted_periods = db.fetch_periods(conn, ticker, 'quarter')
            for r in reviewed_eps():
                if r['ticker'] == ticker:
                    p = next(p for p in persisted_periods if p['period_end'] == r['period_end'])
                    if (p['fields'].get(r['field']) != r['value']
                            or p['field_sources'].get(r['field']) != r['accession']):
                        raise ValueError(f'{ticker}: EPS database readback failed')
                    print(json.dumps({'ticker': ticker, 'eps_verified': r['value'],
                                      'eps_source': r['accession'], 'available_at': p['available_at']}), flush=True)
            db.replace_shares(conn, ticker, shares)
            db.record_events(conn, ticker, events)
            snapshots = []
            for name, rows in [('fundamentals', q + a), ('shares', shares)]:
                snapshot = assess_dataset(name, rows)
                snapshot['payload'] = rows
                snapshots.append(snapshot)
            db.record_dataset_snapshots(conn, ticker, snapshots)
            persisted = db.fetch_shares(conn, ticker)
            identities = {(r['as_of'], r['shares'], r['available_at']) for r in persisted}
            if any((r['as_of'], r['shares'], r['available_at']) not in identities for r in shares):
                raise ValueError(f'{ticker}: share persistence verification failed')
        forecast = compute_and_save(ticker)
        print(json.dumps({'ticker': ticker, 'shares_verified': len(shares),
                          'forecast_status': forecast.get('status'), 'forecast_id': forecast.get('forecast_id'),
                          'alpha_status': (forecast.get('alpha_forecast') or {}).get('status')}), flush=True)
        if forecast.get('status') != 'OK':
            raise ValueError(f'{ticker}: forecast rebuild failed')
    with db.connect() as conn:
        after = build_report(conn)
    Path('data/accounting-recovery-report.json').write_text(json.dumps({'before': before, 'after': after}, indent=2))
    print(json.dumps({'after': after}), flush=True)
    latest_failed = [ticker for ticker in LATEST_LIABILITY_TARGETS
                     if after['latest_quarters'].get(ticker, {}).get('status') != 'PASS']
    if (after['tested'] < before['tested'] or after['failed'] > before['failed']
            or any(r['ticker'] == 'UBER' for r in after['failures']) or latest_failed):
        raise ValueError('Accounting recovery verification failed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
