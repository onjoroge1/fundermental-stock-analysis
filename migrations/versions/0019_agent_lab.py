"""Immutable research journal; deliberately no orders, fills or reward columns."""
from alembic import op

revision = "0019_agent_lab"
down_revision = "0018_consensus_archives"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE agent_lab_policies (
        policy_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
        payload JSONB NOT NULL, recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
    op.execute("""CREATE TABLE agent_lab_evidence (
        input_sha256 TEXT PRIMARY KEY CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL, recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
    op.execute("""CREATE TABLE agent_lab_decisions (
        decision_id UUID PRIMARY KEY,
        policy_id TEXT NOT NULL REFERENCES agent_lab_policies(policy_id),
        ticker TEXT NOT NULL, request_key TEXT NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL, decided_at TIMESTAMPTZ NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        status TEXT NOT NULL CHECK(status IN ('RECORDED','BLOCKED','FAILED')),
        action TEXT NOT NULL CHECK(action IN ('WATCH','NO_TRADE')),
        input_sha256 TEXT NOT NULL REFERENCES agent_lab_evidence(input_sha256),
        payload JSONB NOT NULL,
        UNIQUE(policy_id,ticker,request_key), CHECK(observed_at <= decided_at),
        CHECK((status='RECORDED' AND action='WATCH') OR (status IN ('BLOCKED','FAILED') AND action='NO_TRADE')),
        CHECK((payload->>'mode'='RESEARCH' AND payload->>'execution_status'='NOT_ENABLED') IS TRUE),
        CHECK((payload->>'decision_id'=decision_id::text AND payload->>'ticker'=ticker
            AND payload->>'status'=status AND payload->>'action'=action
            AND payload->>'input_sha256'=input_sha256 AND payload->>'policy_id'=policy_id) IS TRUE),
        CHECK((payload->'pnl'='null'::jsonb AND payload->'reward'='null'::jsonb) IS TRUE))""")
    op.execute("CREATE INDEX agent_lab_decisions_recent ON agent_lab_decisions(policy_id,ticker,recorded_at DESC)")
    op.execute("""CREATE TABLE agent_lab_events (
        sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
        event_id UUID PRIMARY KEY, decision_id UUID NOT NULL REFERENCES agent_lab_decisions(decision_id),
        event_type TEXT NOT NULL CHECK(event_type IN ('RECORDED','REVIEW')),
        request_key TEXT NOT NULL, payload JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(decision_id,request_key))""")
    op.execute("""CREATE TABLE agent_lab_report_outbox (
        event_id UUID PRIMARY KEY REFERENCES agent_lab_events(event_id),
        payload JSONB NOT NULL, recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())""")
    op.execute("""CREATE FUNCTION reject_agent_lab_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Agent Lab records are append-only' USING ERRCODE='55000'; END; $$""")
    for table in ("agent_lab_policies", "agent_lab_evidence", "agent_lab_decisions", "agent_lab_events", "agent_lab_report_outbox"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_agent_lab_mutation()")
        op.execute(f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_agent_lab_mutation()")


def downgrade():
    raise RuntimeError("Agent journal is audit evidence; export and preserve it, never silently drop it")
