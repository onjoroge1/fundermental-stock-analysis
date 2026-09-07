from stock_machine.expectations import consensus_revision


def row(value, observed, source='fmp', period='2026-12-31'):
    return {'snapshot_date':observed[:10],'available_at':observed,'source':source,
            'period_type':'annual','forecast_period_end':period,'eps_mean':value}


def test_intraday_updates_and_reversion_preserve_causal_revision():
    history = [row(1,'2026-01-01T10:00:00Z'),row(2,'2026-02-15T10:00:00Z'),
               row(3,'2026-02-15T11:00:00Z'),row(2,'2026-02-15T12:00:00Z')]
    assert consensus_revision(history,'2026-02-15T10:30:00Z')['eps_revision_pct']==100
    assert consensus_revision(history,'2026-02-15T11:30:00Z')['eps_revision_pct']==200
    assert consensus_revision(history,'2026-02-15T12:30:00Z')['eps_revision_pct']==100


def test_unknown_or_different_provider_cannot_supply_prior():
    history = [row(1,'2026-01-01T10:00:00Z',source='legacy_unattributed'),
               row(2,'2026-02-15T10:00:00Z')]
    assert consensus_revision(history,'2026-02-16')['eps_revision_pct'] is None


def test_revision_window_uses_exact_availability_not_backdated_snapshot():
    old = row(1,'2026-01-17T10:00:00Z');old['snapshot_date']='2026-01-01'
    history=[old,row(2,'2026-02-15T10:00:00Z')]
    assert consensus_revision(history,'2026-02-16T09:00:00Z')['eps_revision_pct'] is None
    assert consensus_revision(history,'2026-02-16T10:00:00Z')['eps_revision_pct']==100


def test_precise_observation_beats_legacy_daily_cache():
    legacy={'snapshot_date':'2026-02-15','period_type':'annual','forecast_period_end':'2026-12-31',
            'source':'legacy_unattributed','eps_mean':10}
    precise=row(2,'2026-02-15T10:00:00Z')
    result=consensus_revision([legacy,precise],'2026-02-16')
    assert result['source']=='fmp'
    assert result['available_at']=='2026-02-15T10:00:00+00:00'


def test_fmp_estimates_record_provider_and_actual_retrieval_time(monkeypatch):
    from datetime import datetime, timezone
    from stock_machine.ingestion import estimates
    monkeypatch.setattr(estimates,'FMP_API_KEY','test')
    monkeypatch.setattr(estimates,'save_raw',lambda *args: None)
    monkeypatch.setattr(estimates,'_plan',{'limit_cap':None,'quarter_estimates':None})
    monkeypatch.setattr(estimates,'_get_adaptive',lambda path,params:
                        ([{'date':'2027-12-31','epsAvg':2}],None) if path.endswith('analyst-estimates') else ([],None))
    before=datetime.now(timezone.utc)
    rows=estimates.fetch_estimates('AAPL')['snapshots']
    after=datetime.now(timezone.utc)
    assert len(rows)==2
    assert all(r['source']=='fmp' and before<=datetime.fromisoformat(r['observed_at'])<=after for r in rows)
