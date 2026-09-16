"""Real PostgreSQL invariants in an isolated schema; never production data."""
import os
from uuid import uuid4

import pytest

from stock_machine import db, research_store
from tests.test_db_integrity import migration_sql
from tests.test_research_integrity import source_bundle


@pytest.fixture
def pg(monkeypatch):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not configured")
    import psycopg
    schema = "test_research_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    def connect():
        return psycopg.connect(dsn, options=f"-c search_path={schema}")
    try:
        with connect() as conn:
            conn.execute(migration_sql("head"))
            conn.execute("INSERT INTO companies(ticker,cik,legal_name) VALUES ('VZ','0000732712','Test VZ fixture')")
        monkeypatch.setattr(db, "connect", connect)
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_evidence_append_only_and_conflicting_key_rolls_back(pg):
    import psycopg
    with pg() as conn:
        first = research_store.save(conn, "EXPERIMENT_PROTOCOL", "test", {"frozen": True})
    with pg() as conn:
        assert research_store.save(conn, "EXPERIMENT_PROTOCOL", "test", {"frozen": True}) == first
    with pytest.raises(ValueError), pg() as conn:
        research_store.save(conn, "EXPERIMENT_PROTOCOL", "test", {"frozen": False})
    for sql in ("UPDATE research_evidence_records SET payload='{}'", "DELETE FROM research_evidence_records", "TRUNCATE research_evidence_records"):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState), pg() as conn:
            conn.execute(sql)
    with pg() as conn:
        assert research_store.get(conn, "EXPERIMENT_PROTOCOL", "test")["payload"] == {"frozen": True}


def test_cycle_report_index_evidence_commit_together_and_replay(pg, monkeypatch, source_bundle):
    from stock_machine import research_contract, research_cycle, control_plane
    monkeypatch.setattr(research_contract, "read_inputs", lambda t: (source_bundle, None, None))
    original = control_plane.save_index_row
    def fail(*a, **k):
        raise RuntimeError("deliberate test failure before index commit")
    monkeypatch.setattr(control_plane, "save_index_row", fail)
    with pytest.raises(RuntimeError):
        research_cycle.run("VZ", "atomic-test", collect_market_data=False)
    with pg() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_reports").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM research_evidence_records").fetchone()[0] == 0
    monkeypatch.setattr(control_plane, "save_index_row", original)
    first = research_cycle.run("VZ", "atomic-test", collect_market_data=False)
    replay = research_cycle.run("VZ", "atomic-test", collect_market_data=False)
    assert not first["replayed"] and replay["replayed"]
    assert first["report_id"] == replay["report_id"]
    with pg() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_reports").fetchone()[0] == 1
        index = conn.execute("SELECT snapshot FROM stock_research_index WHERE ticker='VZ'").fetchone()[0]
        assert index["research_contract"]["snapshot_id"] == first["research_contract"]["snapshot_id"]
        frozen = research_store.latest(conn, "RESEARCH_SNAPSHOT", "VZ")["payload"]
        assert frozen["contract"]["snapshot_id"] == first["research_contract"]["snapshot_id"]
        assert frozen["report"]["claim_validation"]["status"] == "VERIFIED"


def test_repeatable_reader_sees_one_source_report_forecast_view(pg, monkeypatch, source_bundle):
    from stock_machine import bundle, research_contract
    seen = []
    def build(t, *, connection):
        seen.append(connection.execute("SHOW transaction_isolation").fetchone()[0])
        assert connection.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        return source_bundle
    monkeypatch.setattr(bundle, "build_bundle", build)
    b, r, p = research_contract.read_inputs("VZ")
    assert seen == ["repeatable read"]
    assert b == source_bundle and r is None and p is None


def test_full_bundle_reader_is_read_only_with_all_real_dependencies(pg):
    from stock_machine.research_contract import read_inputs
    from stock_machine.normalization.financial_periods import build_periods
    from pathlib import Path
    import json
    raw = json.loads((Path(__file__).parent / "fixtures/vz_2026q2_source_extract.json").read_text())
    quarters, annual, _ = build_periods(raw)
    with pg() as conn:
        db.replace_periods(conn, "VZ", quarters, annual)
    # No monkeypatched bundle helpers: exercises actual monitoring, peers,
    # base rates, events, snapshots and source-backed financial calculations.
    bundle, report, forecast = read_inputs("VZ")
    assert bundle["company"]["ticker"] == "VZ"
    assert bundle["market_snapshot"]["net_debt"] == 163479000000
    assert bundle["peer_group"]["available"] is False
    assert bundle["peer_group"]["comparison"] == []
    assert bundle["price_implied_expectations"]["status"] == "WITHHELD"
    assert bundle["base_rates"]["status"] == "WITHHELD"
    assert report is None and forecast is None


def test_public_coverage_uses_read_only_index_and_keeps_pending_names(pg, monkeypatch, source_bundle):
    from stock_machine import control_plane, webapp, api_v1
    def forbid_rebuild(*args, **kwargs):
        raise AssertionError("public coverage rebuilt a bundle")
    monkeypatch.setattr(webapp, "_companies_live", forbid_rebuild)
    monkeypatch.setattr(control_plane, "build_index_row", forbid_rebuild)
    with pg() as conn:
        conn.execute("INSERT INTO companies(ticker,cik,legal_name) VALUES ('AAPL','0000320193','Apple')")
        control_plane.save_index_row(conn, "VZ", {"ticker": "VZ", "price": 49.5, "indexed_at": "2026-09-16T19:05:00+00:00"}, commit=False)
    rows = webapp.companies()
    api_rows, generated = api_v1._load_coverage_rows()
    assert rows == api_rows
    assert len(rows) == 2 and generated == "2026-09-16T19:05:00+00:00"
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["AAPL"]["index_status"] == "PENDING"
    assert not by_ticker["AAPL"]["research_contract"]["guidance_eligible"]
    assert by_ticker["VZ"]["price"] == 49.5
    with pg() as conn:
        control_plane.coverage_rows(conn)
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "on"
