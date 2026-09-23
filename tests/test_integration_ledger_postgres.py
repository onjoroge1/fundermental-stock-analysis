"""Real append/replay transaction checks in an isolated PostgreSQL schema."""
import os
from uuid import uuid4
import pytest
from datetime import datetime, timezone
from stock_machine.integrations.contracts import ExecutionEvent
from stock_machine.integrations.ledger import append_events, load_events, project


def test_append_replay_and_conflict_are_transactional():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL required")
    import psycopg
    schema = "ledger_"+uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        try:
            with psycopg.connect(dsn, options=f"-c search_path={schema}") as conn:
                conn.execute("""CREATE TABLE research_evidence_records(record_id TEXT PRIMARY KEY,
                    kind TEXT,ticker TEXT,request_key TEXT,content_hash TEXT,payload JSONB,
                    recorded_at TIMESTAMPTZ DEFAULT now(),UNIQUE(kind,request_key))""")
                t = datetime(2020, 1, 2, tzinfo=timezone.utc)
                e = ExecutionEvent(event_id="capital",portfolio_id="paper",engine_version="test",policy_version="test",
                                   source_sha256="a"*64,occurred_at=t,recorded_at=t,kind="CASH",amount=1000)
                assert append_events(conn, [e])["inserted"] == 1
                assert append_events(conn, [e])["replayed"] == 1
                assert project(load_events(conn,"paper"), "2020-01-02")["equity_usd"] == "1000"
                with conn.transaction():
                    with pytest.raises(ValueError, match="CONTENT_CONFLICT"):
                        append_events(conn, [ExecutionEvent(**{**e.model_dump(), "amount": 2000})])
                assert len(load_events(conn,"paper")) == 1
        finally:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
