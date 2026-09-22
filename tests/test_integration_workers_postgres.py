"""Queue concurrency and stale-writer protection against actual PostgreSQL."""
import os
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest


@pytest.fixture
def queue_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn: pytest.skip("TEST_DATABASE_URL required")
    import psycopg
    from stock_machine import control_plane as cp
    schema = "workers_"+uuid4().hex
    def connect(): return psycopg.connect(dsn, options=f"-c search_path={schema}")
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
        try:
            with connect() as conn:
                cp.ensure_schema(conn)
                conn.execute("""CREATE TABLE research_evidence_records(record_id TEXT PRIMARY KEY,kind TEXT,ticker TEXT,
                    request_key TEXT,content_hash TEXT,payload JSONB,recorded_at TIMESTAMPTZ DEFAULT now(),UNIQUE(kind,request_key))""")
            yield connect
        finally:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def seed(connect, key="report"):
    from stock_machine import control_plane as cp
    with connect() as conn:
        return cp.enqueue(conn,"performance_report",payload={"portfolio_id":"legacy-agent-paper-v1"},idempotency_key=key)


def test_default_cron_does_not_claim_heavy_analytics(queue_db):
    from stock_machine.integrations import workers as w
    seed(queue_db)
    with queue_db() as conn:
        assert w.claim_next(conn) is None
        job = w.claim_next(conn,w.WORKER_ONLY)
        assert job["job_type"] == "performance_report"


def test_expired_attempt_cannot_publish_or_checkpoint_after_reclaim(queue_db):
    from stock_machine.integrations import workers as w
    seed(queue_db)
    with queue_db() as conn:
        first = w.claim_next(conn,w.WORKER_ONLY)
        conn.execute("UPDATE orchestration_jobs SET lease_until=now()-interval '1 second'")
        conn.commit()
        second = w.claim_next(conn,w.WORKER_ONLY)
        assert second["attempts"] == first["attempts"]+1
        for operation in (lambda: w.finish(conn, first,{"status":"WRONG"}), lambda: w.checkpoint(conn,first,{}), lambda: w.heartbeat(conn,first)):
            with pytest.raises(w.LeaseLost): operation()
            conn.rollback()
        done = w.finish(conn,second,{"status":"OK"})
        assert done["status"] == "SUCCEEDED"
        assert conn.execute("SELECT count(*) FROM research_evidence_records WHERE kind='WORKER_ARTIFACT_V1'").fetchone()[0] == 1


def test_cancellation_invalidates_lease_and_persists_no_artifact(queue_db):
    from stock_machine.integrations import workers as w
    seed(queue_db)
    with queue_db() as conn:
        job = w.claim_next(conn,w.WORKER_ONLY)
        assert w.cancel(conn,job["job_id"])["cancelled"] is True
        with pytest.raises(w.LeaseLost): w.finish(conn,job,{"status":"TOO_LATE"})
        conn.rollback()
        assert conn.execute("SELECT count(*) FROM research_evidence_records").fetchone()[0] == 0


def test_concurrent_claims_return_one_owner(queue_db):
    from stock_machine.integrations import workers as w
    seed(queue_db)
    def claim(_):
        with queue_db() as conn: return w.claim_next(conn,w.WORKER_ONLY)
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = list(executor.map(claim,range(2)))
    assert sum(job is not None for job in jobs) == 1


def test_retry_errors_are_redacted_and_idempotency_conflicts_rejected(queue_db):
    from stock_machine.integrations import workers as w
    from stock_machine import control_plane as cp
    seed(queue_db)
    with queue_db() as conn:
        with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
            cp.enqueue(conn,"performance_report",payload={"different":True},idempotency_key="report")
        conn.rollback()
        job = w.claim_next(conn,w.WORKER_ONLY)
        row = w.fail(conn,job,ValueError("secret-postgres-dsn"))
        assert row["status"] == "PENDING" and row["last_error"] == "ValueError"
        assert w.claim_next(conn,w.WORKER_ONLY) is None
