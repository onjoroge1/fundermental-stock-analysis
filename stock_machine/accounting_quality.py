"""Read-only accounting identity diagnostics with an explicit coverage denominator."""
from collections import Counter

from .kpis import RECON_TOLERANCE

CORE = ('total_assets', 'total_liabilities', 'shareholders_equity')
COMPONENTS = ('noncontrolling_interest', 'temporary_equity', 'redeemable_noncontrolling_interest')


def summarize_periods(periods: list[dict]) -> dict:
    passed = tested = 0
    failures = []
    missing = Counter()
    latest = {}
    for p in periods:
        f = p['fields']
        absent = [k for k in CORE if f.get(k) is None]
        if absent or f['total_assets'] <= 0:
            status = 'UNTESTED'
            for k in absent or ['nonpositive_assets']:
                missing[k] += 1
        else:
            tested += 1
            residual = f['total_assets'] - sum(f[k] for k in CORE[1:]) - sum(f.get(k) or 0 for k in COMPONENTS)
            ok = abs(residual) <= RECON_TOLERANCE * f['total_assets']
            passed += int(ok)
            status = 'PASS' if ok else 'FAIL'
            if not ok:
                sources = {k: p.get('field_sources', {}).get(k) for k in CORE + COMPONENTS if f.get(k) is not None}
                failures.append({'ticker': p['ticker'], 'period_end': p['period_end'],
                                 'duration_type': p['duration_type'], 'residual': residual,
                                 'residual_pct_assets': round(100 * residual / f['total_assets'], 4),
                                 'fields': {k: f.get(k) for k in CORE + COMPONENTS},
                                 'field_sources': sources,
                                 'mixed_source_accessions': len({s for s in sources.values() if s}) > 1})
        if p['duration_type'] == 'quarter' and p['period_end'] > latest.get(p['ticker'], {}).get('period_end', ''):
            latest[p['ticker']] = {'period_end': p['period_end'], 'status': status, 'missing_fields': absent}
    return {'read_only': True, 'tolerance_pct_assets': 100 * RECON_TOLERANCE,
            'periods': len(periods), 'tested': tested, 'passed': passed,
            'failed': tested - passed, 'untested': len(periods) - tested,
            'tested_coverage_pct': round(100 * tested / len(periods), 2) if periods else None,
            'pass_rate_pct': round(100 * passed / tested, 2) if tested else None,
            'missing_field_counts': dict(missing),
            'failures': sorted(failures, key=lambda r: (r['ticker'], r['period_end'], r['duration_type'])),
            'latest_quarters': dict(sorted(latest.items())),
            'limitations': ['Arithmetic identity only; does not certify source/entity consistency.',
                            'Untested periods are not passes. Annual and quarterly rows can share a balance sheet.',
                            'Mixed filing accessions require review; they do not alone prove an error.']}


def build_report(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute('SELECT ticker,period_end::text,duration_type,fields,field_sources FROM financial_periods')
        periods = [dict(zip(('ticker','period_end','duration_type','fields','field_sources'), row)) for row in cur.fetchall()]
    return summarize_periods(periods)
