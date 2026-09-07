from datetime import timedelta

import pytest

from stock_machine.asof import utc_timestamp
from stock_machine.macro_archive import parse_vintage
from stock_machine.macro import features_as_of
from stock_machine.backtest import options_panel
from stock_machine.expectations import known_surprises


def test_archive_requires_exact_vintage_header():
    for header in ('observation_date,VIXCLS', 'observation_date,VIXCLS_20260102', '<html>'):
        with pytest.raises(ValueError):
            parse_vintage(header + '\n2026-01-01,15\n', 'VIXCLS', '2026-01-01', 'test')


def test_archive_day_is_only_known_after_midnight_and_later_revision_cannot_leak():
    rows = parse_vintage('observation_date,VIXCLS_20260101\n2025-12-31,15\n', 'VIXCLS', '2026-01-01', 'test')
    assert features_as_of({'VIXCLS': rows}, '2026-01-01')['features']['has_vix'] == 0
    before = features_as_of({'VIXCLS': rows}, '2026-01-02')['features']
    rows.append({**rows[0], 'available_at': '2026-01-02 01:00:00+00', 'value': 90})
    assert features_as_of({'VIXCLS': rows}, '2026-01-02')['features'] == before
    assert before['vix_level'] == 15


@pytest.mark.parametrize('data', ['2026-01-02,12', '2026-01-01,nan'])
def test_archive_rejects_future_and_nonfinite_values(data):
    with pytest.raises(ValueError):
        parse_vintage('observation_date,VIXCLS_20260101\n' + data, 'VIXCLS', '2026-01-01', 'test')


def test_macro_orders_revisions_by_instant_not_timestamp_spelling():
    rows = [
        {'observation_date': '2026-01-01', 'available_at': '2026-01-02T00:00:00+02:00', 'value': 10},
        {'observation_date': '2026-01-01', 'available_at': '2026-01-01 23:00:00+00', 'value': 20},
    ]
    assert features_as_of({'VIXCLS': rows}, '2026-01-02')['features']['vix_level'] == 20


def test_macro_stale_history_does_not_count_as_coverage():
    rows = [{'observation_date': '2025-12-01', 'available_at': '2025-12-02', 'value': 15}]
    assert features_as_of({'VIXCLS': rows}, '2026-01-01')['features']['has_vix'] == 0


def test_treasury_tenors_align_on_same_economic_date():
    def row(day, value):
        return {'observation_date': day, 'available_at': day, 'value': value}
    series = {'DGS2': [row('2026-01-01', 4), row('2026-01-02', 10)],
              'DGS10': [row('2026-01-01', 5)]}
    assert features_as_of(series, '2026-01-03')['features']['curve_10y2y'] == 1


@pytest.mark.parametrize('offset,feature,matched', [
    (timedelta(hours=1), {'atm_iv': .2}, 0),
    (timedelta(days=-10, seconds=-1), {'atm_iv': .2}, 0),
    (timedelta(days=-10), {'atm_iv': .2}, 1),
    (timedelta(hours=-1), {}, 0),
    (timedelta(hours=-1), {'has_atm_iv': 0}, 0),
    (timedelta(hours=-1), {'atm_iv': float('nan')}, 0),
])
def test_options_require_usable_features_before_exact_cutoff(monkeypatch, offset, feature, matched):
    target = utc_timestamp('2026-01-15')
    monkeypatch.setattr(options_panel, '_load', lambda *_: ([target + offset], [feature]))
    rows, coverage = options_panel.enrich(None, [{'ticker': 'AAPL', 'as_of': '2026-01-15'}])
    assert coverage['matched_option_surfaces'] == matched
    assert coverage['tickers_with_matches'] == matched
    assert rows[0]['options_implied']['available'] == bool(matched)


def test_future_earnings_event_is_not_known_despite_bad_availability():
    assert known_surprises([{'date': '2026-04-01', 'available_at': '2026-01-01',
                             'surprise_pct': 20}], '2026-02-01') == []
