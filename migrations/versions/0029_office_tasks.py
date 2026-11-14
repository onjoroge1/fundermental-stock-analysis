"""Trading Office work queue: agent definitions, tasks, dependencies and run attempts.

Additive only. It creates four new ``office_`` tables and alters no existing table, so application code
written for 0028 keeps working on a database at 0029 (see ``db.COMPATIBLE_SCHEMA_VERSIONS``).
"""
from alembic import op

revision = "0029_office_tasks"
down_revision = "0028_fair_value_forward"
branch_labels = None
depends_on = None

TASK_STATES = ("QUEUED", "RUNNING", "WAITING_DATA", "WAITING_REVIEW", "RETRY_WAIT",
               "COMPLETED", "BLOCKED", "FAILED", "CANCELLED")
RUN_OUTCOMES = ("COMPLETED", "RETRY", "FAILED", "BLOCKED", "WAITING", "LEASE_EXPIRED")
PROVIDERS = ("none", "fake", "anthropic", "openai")
SAFE_CODE = "^[A-Z][A-Z0-9_]{1,79}$"


def _in(values):
    return ",".join(f"'{v}'" for v in values)


def upgrade():
    # Fail fast rather than queue behind a long transaction; these are new tables, so the locks are brief.
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute(f"""CREATE TABLE office_agent_definitions (
        role TEXT NOT NULL CHECK(role ~ '^[a-z][a-z_]{{1,39}}$'),
        version INT NOT NULL CHECK(version > 0),
        provider TEXT NOT NULL CHECK(provider IN ({_in(PROVIDERS)})),
        model TEXT NOT NULL DEFAULT '',
        definition JSONB NOT NULL,
        definition_sha256 TEXT NOT NULL CHECK(definition_sha256 ~ '^[0-9a-f]{{64}}$'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(role, version))""")
    op.execute("""CREATE FUNCTION reject_office_definition_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Office agent definitions are immutable; add a new version'
            USING ERRCODE='55000'; END; $$""")
    op.execute("CREATE TRIGGER office_agent_definitions_immutable BEFORE UPDATE OR DELETE "
               "ON office_agent_definitions FOR EACH ROW EXECUTE FUNCTION reject_office_definition_mutation()")
    op.execute("CREATE TRIGGER office_agent_definitions_no_truncate BEFORE TRUNCATE "
               "ON office_agent_definitions FOR EACH STATEMENT EXECUTE FUNCTION reject_office_definition_mutation()")
    op.execute(f"""CREATE TABLE office_tasks (
        task_id UUID PRIMARY KEY,
        logical_key TEXT NOT NULL UNIQUE CHECK(length(logical_key) BETWEEN 1 AND 200),
        task_type TEXT NOT NULL CHECK(task_type ~ '^[a-z][a-z0-9_]{{1,59}}$'),
        payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        state TEXT NOT NULL DEFAULT 'QUEUED' CHECK(state IN ({_in(TASK_STATES)})),
        priority INT NOT NULL DEFAULT 100 CHECK(priority BETWEEN 0 AND 1000),
        due_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        retry_at TIMESTAMPTZ,
        attempts INT NOT NULL DEFAULT 0 CHECK(attempts >= 0),
        max_attempts INT NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
        lease_owner TEXT,
        lease_token UUID,
        lease_until TIMESTAMPTZ,
        result JSONB,
        error_code TEXT CHECK(error_code IS NULL OR error_code ~ '{SAFE_CODE}'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT office_tasks_attempts_bounded CHECK(attempts <= max_attempts),
        CONSTRAINT office_tasks_lease_iff_running CHECK(
            (state = 'RUNNING') = (lease_token IS NOT NULL)
            AND (lease_token IS NULL) = (lease_until IS NULL)
            AND (lease_token IS NULL) = (lease_owner IS NULL)),
        CONSTRAINT office_tasks_retry_time CHECK((state = 'RETRY_WAIT') = (retry_at IS NOT NULL)))""")
    op.execute("CREATE INDEX office_tasks_claimable ON office_tasks(priority, due_at, created_at) "
               "WHERE state IN ('QUEUED','RETRY_WAIT')")
    op.execute("CREATE INDEX office_tasks_running ON office_tasks(lease_until) WHERE state = 'RUNNING'")
    op.execute("""CREATE TABLE office_task_dependencies (
        task_id UUID NOT NULL REFERENCES office_tasks(task_id),
        depends_on UUID NOT NULL REFERENCES office_tasks(task_id),
        PRIMARY KEY(task_id, depends_on),
        CHECK(task_id <> depends_on))""")
    op.execute("CREATE INDEX office_task_dependencies_parent ON office_task_dependencies(depends_on)")
    op.execute(f"""CREATE TABLE office_runs (
        run_id UUID PRIMARY KEY,
        task_id UUID NOT NULL REFERENCES office_tasks(task_id),
        attempt INT NOT NULL CHECK(attempt > 0),
        actor TEXT NOT NULL CHECK(length(actor) BETWEEN 1 AND 120),
        lease_token UUID NOT NULL,
        started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ,
        outcome TEXT CHECK(outcome IN ({_in(RUN_OUTCOMES)})),
        error_code TEXT CHECK(error_code IS NULL OR error_code ~ '{SAFE_CODE}'),
        external_call_open BOOLEAN NOT NULL DEFAULT false,
        code_version TEXT NOT NULL DEFAULT '',
        config_version TEXT NOT NULL DEFAULT '',
        model_version TEXT NOT NULL DEFAULT '',
        usage JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        cost_usd NUMERIC(12,6) CHECK(cost_usd IS NULL OR cost_usd >= 0),
        github_run_id TEXT,
        UNIQUE(task_id, attempt),
        UNIQUE(lease_token),
        CONSTRAINT office_runs_finished_has_outcome CHECK((finished_at IS NULL) = (outcome IS NULL)))""")
    op.execute("CREATE INDEX office_runs_open ON office_runs(task_id) WHERE finished_at IS NULL")


def downgrade():
    raise RuntimeError("Preserve office work history; use a reviewed forward migration")
