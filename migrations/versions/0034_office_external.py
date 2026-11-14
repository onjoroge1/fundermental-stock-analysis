"""Trading Office mirrored local records: results summaries uploaded from the owner's machine.

Additive only. It creates one new ``office_`` table and alters no existing table. A row is a summary of a
record kept elsewhere under frozen rules. It is a simulation, never a broker fill, and is written once.
"""
from alembic import op

revision = "0034_office_external"
down_revision = "0033_office_paper"
branch_labels = None
depends_on = None

SHA = "~ '^[0-9a-f]{64}$'"


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute(f"""CREATE TABLE office_external_records (
        record_id UUID PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        version TEXT NOT NULL,
        record_name TEXT NOT NULL CHECK(record_name ~ '^[A-Za-z0-9._ -]{{1,60}}$'),
        first_session DATE NOT NULL,
        through_session DATE NOT NULL,
        sessions INT NOT NULL CHECK(sessions >= 0),
        trades INT NOT NULL CHECK(trades >= 0),
        trade_dates INT NOT NULL CHECK(trade_dates >= 0),
        figures JSONB NOT NULL,
        rule_hashes JSONB NOT NULL,
        source_file TEXT NOT NULL,
        source_sha256 TEXT NOT NULL CHECK(source_sha256 {SHA}),
        payload_sha256 TEXT NOT NULL CHECK(payload_sha256 {SHA}),
        generated_at TIMESTAMPTZ NOT NULL,
        received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        uploaded_by TEXT NOT NULL CHECK(length(uploaded_by) BETWEEN 1 AND 80),
        simulated BOOLEAN NOT NULL DEFAULT true CHECK(simulated),
        broker_fill BOOLEAN NOT NULL DEFAULT false CHECK(NOT broker_fill),
        FOREIGN KEY(strategy_id, version) REFERENCES office_strategy_versions(strategy_id, version),
        UNIQUE(strategy_id, version, through_session, source_sha256),
        CONSTRAINT office_external_records_sessions_in_order CHECK(first_session <= through_session),
        CONSTRAINT office_external_records_counts CHECK(trade_dates <= trades AND trade_dates <= sessions))""")
    op.execute("CREATE INDEX office_external_records_latest ON office_external_records"
               "(strategy_id, version, through_session DESC, received_at DESC)")
    op.execute("""CREATE FUNCTION reject_office_external_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Office mirrored records are written once' USING ERRCODE='55000'; END; $$""")
    op.execute("CREATE TRIGGER office_external_records_immutable BEFORE UPDATE OR DELETE ON office_external_records "
               "FOR EACH ROW EXECUTE FUNCTION reject_office_external_change()")
    op.execute("CREATE TRIGGER office_external_records_no_truncate BEFORE TRUNCATE ON office_external_records "
               "FOR EACH STATEMENT EXECUTE FUNCTION reject_office_external_change()")
    # Only a strategy kept on the owner's machine can be mirrored, and only with the hashes the inventory froze.
    op.execute("""CREATE FUNCTION check_office_external_record() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM office_strategy_versions s
                           WHERE s.strategy_id = NEW.strategy_id AND s.version = NEW.version
                             AND s.admissible AND s.execution_kind = 'EXTERNAL_LOCAL'
                             AND s.record->'hashes' = NEW.rule_hashes) THEN
                RAISE EXCEPTION 'An office mirrored record needs a local strategy version and its frozen hashes'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER office_external_records_checked BEFORE INSERT ON office_external_records "
               "FOR EACH ROW EXECUTE FUNCTION check_office_external_record()")


def downgrade():
    raise RuntimeError("Preserve the office mirrored records; use a reviewed forward migration")
