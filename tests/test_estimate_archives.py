from copy import deepcopy
import pytest

from stock_machine.ingestion.estimate_archives import validate_archive
from stock_machine.expectations import consensus_revision


def archive():
    """Synthetic fixture only; these are not actual analyst observations."""
    return {'format': 'pit-consensus-v1', 'point_in_time': True,
            'provider': 'factset', 'dataset': 'synthetic_test',
            'license_reference': 'synthetic fixture', 'source_reference': 'synthetic fixture',
            'timestamp_methodology': 'synthetic vendor snapshot plus dissemination lag',
            'original_file_sha256': 'a' * 64, 'retrieved_at': '2026-09-01T10:00:00Z',
            'records': [{'ticker': 'AAPL', 'cik': '320193', 'security_id': 'synthetic',
                'snapshot_at': '2020-01-01T20:00:00Z', 'available_at': '2020-01-02T01:00:00Z',
                'period_type': 'annual', 'forecast_period_end': '2020-12-31',
                'currency': 'USD', 'eps_basis': 'normalized', 'eps_type': 'diluted',
                'share_basis': 'contemporaneous', 'eps_mean': 2, 'analyst_count': 10}]}


@pytest.mark.parametrize('key,value', [
    ('available_at', '2019-12-31T20:00:00Z'), ('available_at', '2020-01-01'),
    ('snapshot_at', '2020-01-01T20:00:00'), ('eps_mean', float('nan')),
    ('eps_mean', float('inf')), ('eps_mean', True), ('currency', 'EUR'),
    ('eps_basis', 'unknown'), ('eps_type', 'basic'), ('analyst_count', 0),
    ('share_basis', 'split_adjusted_to_today'), ('cik', None), ('security_id', ''),
    ('eps_low', 3), ('eps_high', 1), ('revenue_mean', 10),
])
def test_archive_rejects_unsafe_observations(key, value):
    a = archive(); a['records'][0][key] = value
    with pytest.raises((ValueError, TypeError)):
        validate_archive(a)


def test_current_fmp_cannot_be_relabelled_as_archive():
    a = archive(); a['provider'] = 'fmp'
    with pytest.raises(ValueError):
        validate_archive(a)


def test_conflicting_duplicate_rejected_and_identical_replay_deduplicated():
    a = archive(); a['records'].append(deepcopy(a['records'][0]))
    assert len(validate_archive(a)) == 1
    a['records'][1]['eps_mean'] = 3
    with pytest.raises(ValueError):
        validate_archive(a)


def test_archive_reader_contract_uses_dissemination_time_and_same_eps_basis():
    a = archive(); later = deepcopy(a['records'][0])
    later.update(snapshot_at='2020-02-15T20:00:00Z', available_at='2020-02-16T01:00:00Z', eps_mean=3)
    a['records'].append(later)
    def history(a):
        return [{**r['payload'], **r, 'snapshot_date': r['observed_at'][:10],
                 'available_at': r['observed_at']} for r in validate_archive(a)]
    assert not consensus_revision(history(a), '2020-01-02')['has_consensus']
    assert consensus_revision(history(a), '2020-02-16T00:00:00Z')['eps_revision_pct'] is None
    assert consensus_revision(history(a), '2020-02-16T02:00:00Z')['eps_revision_pct'] == 50
    a['records'][1]['eps_basis'] = 'gaap'
    assert consensus_revision(history(a), '2020-02-16T02:00:00Z')['eps_revision_pct'] is None
