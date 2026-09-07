from stock_machine.accounting_quality import summarize_periods
from stock_machine.normalization.financial_periods import build_periods


def test_uber_minority_interest_alias_retains_original_filing_availability():
    tags = {'Assets': 32292000000, 'Liabilities': 16241000000,
            'StockholdersEquity': 15062000000, 'NonredeemableNoncontrollingInterest': 680000000,
            'RedeemableNoncontrollingInterestEquityCarryingAmount': 309000000}
    gaap = {tag: {'units': {'USD': [{'val': val, 'end': '2019-09-30', 'filed': '2019-11-05',
                                    'accn': 'uber-q3', 'form': '10-Q'}]}} for tag, val in tags.items()}
    gaap['Revenues'] = {'units': {'USD': [{'val': 3813000000, 'start': '2019-07-01', 'end': '2019-09-30',
                                         'filed': '2019-11-05', 'accn': 'uber-q3', 'form': '10-Q'}]}}
    q, _, _ = build_periods({'facts': {'us-gaap': gaap}})
    assert q[0]['fields']['noncontrolling_interest'] == 680000000
    assert q[0]['field_sources']['noncontrolling_interest'] == 'uber-q3'
    assert q[0]['available_at'] == '2019-11-06'
    assert summarize_periods([dict(q[0], ticker='UBER')])['passed'] == 1


def test_missing_core_fields_are_untested_and_mixed_sources_remain_visible():
    base = {'ticker': 'TEST', 'period_end': '2026-06-30', 'duration_type': 'quarter'}
    report = summarize_periods([
        dict(base, fields={'total_assets': 100, 'shareholders_equity': 50}),
        dict(base, ticker='HIMS', fields={'total_assets': 205, 'total_liabilities': 12,
             'shareholders_equity': 5, 'temporary_equity': 250, 'redeemable_noncontrolling_interest': 188},
             field_sources={'total_assets': 'spac', 'temporary_equity': 'operating-company'}),
    ])
    assert (report['periods'], report['tested'], report['passed'], report['failed'], report['untested']) == (2, 1, 0, 1, 1)
    assert report['tested_coverage_pct'] == 50
    assert report['failures'][0]['residual'] == -250
    assert report['failures'][0]['mixed_source_accessions'] is True
    assert report['latest_quarters']['TEST']['status'] == 'UNTESTED'
