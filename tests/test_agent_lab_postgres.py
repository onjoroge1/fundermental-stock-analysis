"""Real PostgreSQL tests, isolated from production and other migration tests."""
import importlib.util
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from stock_machine.agents import journal
from stock_machine.agents.contracts import CaptureRequest, ReviewRequest


@pytest.fixture
def pg(monkeypatch):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    schema = "test_agent_lab_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    def connect():
        return psycopg.connect(dsn, options=f"-c search_path={schema}")
    try:
        migration = Path(__file__).parents[1] / "migrations" / "versions" / "0019_agent_lab.py"
        spec = importlib.util.spec_from_file_location("agent_migration_test", migration)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with connect() as conn:
            module.op = SimpleNamespace(execute=conn.execute)
            module.upgrade()
        monkeypatch.setattr(journal, "connection", connect)
        monkeypatch.setattr(journal, "expected_session", lambda: "2024-01-05")
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def source(_ticker):
    # Dedicated test fixture. Not seeded into runtime storage.
    versions = {name: {"status": "PASS", "content_hash": "sha256:" + "a" * 64,
                "observed_at": "2024-01-06T10:00:00Z"} for name in ("prices", "fundamentals", "filings")}
    return {"ticker": "AAPL", "generated_at": "2024-01-06T11:00:00Z",
        "market_snapshot": {"price": 100, "price_date": "2024-01-05"},
        "data_quality": {"status": "PASS", "dataset_versions": versions},
        "analysis": {"report_available": True, "investment_thesis": {"summary": "Synthetic integration-test fixture."}}}


def capture(key="test-request-001", reader=source):
    return journal.capture("AAPL", CaptureRequest(idempotency_key=key), reader=reader)


def count(connect, table):
    with connect() as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_replay_is_single_decision_and_atomic_report(pg):
    calls = []
    def reader(ticker):
        calls.append(ticker)
        return source(ticker)
    first = capture(reader=reader)
    second = capture(reader=reader)
    assert first["decision"] == second["decision"]
    assert second["replayed"] and calls == ["AAPL"]
    for table in ("agent_lab_decisions", "agent_lab_events", "agent_lab_report_outbox"):
        assert count(pg, table) == 1
    item = journal.detail(first["decision"]["decision_id"])
    assert item["frozen_evidence"] == source("AAPL")


def test_concurrent_duplicate_delivery_reads_once(pg):
    calls = []
    def reader(t):
        calls.append(t)
        time.sleep(0.1)
        return source(t)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: capture(reader=reader), range(2)))
    assert calls == ["AAPL"]
    assert results[0]["decision"]["decision_id"] == results[1]["decision"]["decision_id"]
    assert count(pg, "agent_lab_decisions") == 1


def test_failed_outbox_rolls_back_decision(pg, monkeypatch):
    def fail(*args):
        raise RuntimeError("test outbox failure")
    monkeypatch.setattr(journal, "_event", fail)
    with pytest.raises(RuntimeError):
        capture()
    assert count(pg, "agent_lab_decisions") == 0
    assert count(pg, "agent_lab_evidence") == 0
    assert count(pg, "agent_lab_policies") == 0


def test_update_delete_truncate_are_denied(pg):
    import psycopg
    capture()
    for table in ("agent_lab_policies", "agent_lab_evidence", "agent_lab_decisions", "agent_lab_events", "agent_lab_report_outbox"):
        for sql in (f"UPDATE {table} SET payload=payload", f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
            with pytest.raises(psycopg.Error):
                with pg() as conn:
                    conn.execute(sql)
    assert count(pg, "agent_lab_decisions") == 1


def test_review_appends_and_changed_retry_conflicts(pg):
    original = capture()["decision"]
    request = ReviewRequest(idempotency_key="review-001", message="Recheck source at the next release.")
    journal.append_review(original["decision_id"], request)
    journal.append_review(original["decision_id"], request)
    with pytest.raises(journal.JournalConflict):
        journal.append_review(original["decision_id"], ReviewRequest(idempotency_key="review-001", message="Different content"))
    item = journal.detail(original["decision_id"])
    assert item["decision"] == original
    assert len(item["events"]) == 2
    assert count(pg, "agent_lab_report_outbox") == 2


def test_reader_failure_is_preserved_without_secret(pg):
    def fail(_ticker):
        raise RuntimeError("password=VERY_SECRET; broker_token=VERY_SECRET")
    decision = capture(reader=fail)["decision"]
    assert decision["status"] == "FAILED"
    item = journal.detail(decision["decision_id"])
    assert "VERY_SECRET" not in str(item)
    assert count(pg, "agent_lab_report_outbox") == 1


def test_totals_not_derived_from_paginated_cards(pg):
    a = capture("request-one")["decision"]
    b = capture("request-two")["decision"]
    capture("request-three")
    assert b["previous_decision_id"] == a["decision_id"]
    dashboard = journal.dashboard(limit=1)
    assert dashboard["counts"]["total"] == 3
    assert len(dashboard["decisions"]) == 1 and dashboard["truncated"]
    assert dashboard["execution"]["pnl"] is None
    from datetime import date
    empty = journal.dashboard(day=date(2000, 1, 1))
    assert empty["counts"]["total"] == 0
    assert empty["scope"]["day_utc"] == "2000-01-01"


def test_execution_events_and_orphans_rejected(pg):
    import psycopg
    d = capture()["decision"]
    for decision_id, event_type in ((d["decision_id"], "FILLED"), (str(uuid4()), "REVIEW")):
        with pytest.raises(psycopg.Error):
            with pg() as conn:
                conn.execute("INSERT INTO agent_lab_events (event_id,decision_id,event_type,request_key,payload) VALUES (%s,%s,%s,%s,'{}')", (str(uuid4()), decision_id, event_type, "bad-event"))
