"""Append-only source observations, research cycles and prospective experiments."""
from alembic import op

revision = "0020_research_integrity"
down_revision = "0019_agent_lab"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE research_evidence_records (
        record_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('RAW_SOURCE','BALANCE_AUDIT','CLAIM_AUDIT',
            'PROVIDER_DAILY','RESEARCH_SNAPSHOT','CYCLE_RESULT','EXPERIMENT_PROTOCOL',
            'EXPERIMENT_FORECAST','EXPERIMENT_OUTCOME')),
        ticker TEXT, request_key TEXT NOT NULL,
        content_hash TEXT NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
        payload JSONB NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(kind,request_key))""")
    op.execute("CREATE INDEX research_evidence_recent ON research_evidence_records(kind,ticker,recorded_at DESC)")
    op.execute("""CREATE FUNCTION reject_research_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Research evidence is append-only' USING ERRCODE='55000'; END; $$""")
    op.execute("CREATE TRIGGER research_evidence_immutable BEFORE UPDATE OR DELETE ON research_evidence_records FOR EACH ROW EXECUTE FUNCTION reject_research_evidence_mutation()")
    op.execute("CREATE TRIGGER research_evidence_no_truncate BEFORE TRUNCATE ON research_evidence_records FOR EACH STATEMENT EXECUTE FUNCTION reject_research_evidence_mutation()")


def downgrade():
    raise RuntimeError("Preserve prospective experiment and source evidence; no destructive downgrade")
