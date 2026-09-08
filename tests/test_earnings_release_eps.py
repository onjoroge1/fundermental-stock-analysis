from copy import deepcopy

from stock_machine.normalization.earnings_releases import (
    eps_provenance, reviewed_eps, supplement_quarters,
)
from stock_machine.normalization.financial_periods import build_periods


def period(r):
    return {'period_start': r['period_start'], 'period_end': r['period_end'],
            'fields': {}, 'field_sources': {}, 'filed_at': '2026-09-02',
            'available_at': '2026-09-03', 'derived': True}


def test_all_reviewed_quarters_recover_exact_values_and_keep_latest_dependency():
    for r in reviewed_eps():
        p = period(r); events = []
        supplement_quarters(r['cik'], [p], events)
        assert p['fields']['diluted_eps'] == r['value']
        assert p['available_at'] == '2026-09-03'
        assert p['derived']  # additive fields remain derived; EPS is sourced
        assert eps_provenance(p)['diluted_eps']['source_sha256'] == r['source_sha256']
        assert len(events) == 1


def test_wrong_entity_period_and_existing_fact_never_overwritten():
    r = reviewed_eps()[0]
    p = period(r); before = deepcopy(p)
    supplement_quarters(9999999999, [p], [])
    assert p == before
    p['period_start'] = '2026-01-01'
    supplement_quarters(r['cik'], [p], [])
    assert not p['fields']
    p = period(r); p['fields']['diluted_eps'] = 9.99
    events = []
    supplement_quarters(r['cik'], [p], events)
    assert p['fields']['diluted_eps'] == 9.99
    assert events[0]['event'] == 'EARNINGS_RELEASE_EPS_CONFLICT'
    assert not eps_provenance(p)


def test_later_release_never_enters_earlier_information_set():
    r = reviewed_eps()[0]; p = period(r)
    p.update(filed_at='2026-07-01', available_at='2026-07-02')
    supplement_quarters(r['cik'], [p], [])
    assert p['filed_at'] == '2026-08-06'
    assert p['available_at'] == '2026-08-07'


def test_q4_starts_after_q3_and_release_does_not_create_incomplete_period():
    from tests.test_financial_periods import synthetic_facts
    q, _, _ = build_periods(synthetic_facts())
    q4 = next(p for p in q if p['derived'])
    assert q4['period_start'] == '2025-07-01'
    assert 'diluted_eps' not in q4['fields']
    r = reviewed_eps()[0]
    assert build_periods({'cik': r['cik'], 'facts': {}})[0] == []


def test_recovered_split_eps_does_not_enable_a_mixed_basis_ttm():
    from stock_machine.features.metrics import build_ttm
    r = next(r for r in reviewed_eps() if r['ticker'] == 'KLAC')
    qs = []
    for start, end in [('2025-07-01', '2025-09-30'), ('2025-10-01', '2025-12-31'),
                       ('2026-01-01', '2026-03-31'), ('2026-04-01', '2026-06-30')]:
        p = period(r)
        p.update(period_start=start, period_end=end)
        p['fields'].update(diluted_eps=9, revenue=100, net_income=10, weighted_average_diluted_shares=1)
        qs.append(p)
    qs[-1]['fields'].pop('diluted_eps')
    supplement_quarters(r['cik'], qs, [])
    ttm = build_ttm(qs)
    assert qs[-1]['fields']['diluted_eps'] == 1.04
    assert 'diluted_eps' not in ttm['fields']
    assert ttm['fields']['revenue'] == 400
    assert ttm['eps_basis_status'] == 'MIXED_SPLIT_BASIS_WITHHELD'
