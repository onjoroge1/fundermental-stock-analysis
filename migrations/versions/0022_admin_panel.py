"""Owner sessions, audit history, operational capture pause and resumable pilot."""
from alembic import op

revision = "0022_admin_panel"
down_revision = "0021_monitoring_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE operator_users (
        username TEXT PRIMARY KEY CHECK(username='admin'),
        password_hash TEXT NOT NULL CHECK(password_hash LIKE '$argon2id$%'),
        must_change_password BOOLEAN NOT NULL DEFAULT true,
        disabled BOOLEAN NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE operator_sessions (
        token_hash TEXT PRIMARY KEY CHECK(length(token_hash)=64),
        username TEXT NOT NULL REFERENCES operator_users(username),
        csrf_token TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
        last_seen TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE INDEX operator_sessions_expiry ON operator_sessions(expires_at)")
    op.execute("""CREATE TABLE operator_auth_limits (
        singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
        window_start TIMESTAMPTZ NOT NULL DEFAULT now(), attempts INT NOT NULL DEFAULT 0)""")
    op.execute("INSERT INTO operator_auth_limits DEFAULT VALUES")
    op.execute("""CREATE TABLE operator_controls (
        singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
        capture_paused BOOLEAN NOT NULL DEFAULT false,
        version INT NOT NULL DEFAULT 1 CHECK(version > 0),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    # Preserve the already authorized deployment switch. No new enabling default.
    op.execute("INSERT INTO operator_controls DEFAULT VALUES")
    op.execute("""CREATE TABLE operator_audit (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        actor TEXT NOT NULL, event TEXT NOT NULL,
        details JSONB NOT NULL, occurred_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("""CREATE FUNCTION reject_operator_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Operator audit is append-only' USING ERRCODE='55000'; END; $$""")
    op.execute("CREATE TRIGGER operator_audit_immutable BEFORE UPDATE OR DELETE ON operator_audit FOR EACH ROW EXECUTE FUNCTION reject_operator_audit_mutation()")
    op.execute("CREATE TRIGGER operator_audit_no_truncate BEFORE TRUNCATE ON operator_audit FOR EACH STATEMENT EXECUTE FUNCTION reject_operator_audit_mutation()")
    op.execute("""CREATE TABLE operator_pilot_runs (
        run_id UUID PRIMARY KEY, actor TEXT NOT NULL REFERENCES operator_users(username),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE operator_pilot_items (
        run_id UUID NOT NULL REFERENCES operator_pilot_runs(run_id),
        ticker TEXT NOT NULL CHECK(ticker IN ('AAPL','MSFT','UBER','HIMS','VZ')),
        status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
        attempts INT NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
        lease_until TIMESTAMPTZ, lease_token UUID, result JSONB,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(run_id,ticker))""")


def downgrade():
    raise RuntimeError("Preserve owner audit and research evidence; use a reviewed forward migration")
