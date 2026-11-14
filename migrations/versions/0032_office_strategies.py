"""Trading Office strategy inventory, paper books, admission decisions and deployments.

Additive only. It creates four new ``office_`` tables and alters no existing table. Inventory rows, books
and admission decisions are written once. A deployment can only change its state, and no state is live.
"""
from alembic import op

revision = "0032_office_strategies"
down_revision = "0031_office_research"
branch_labels = None
depends_on = None

SHA = "~ '^[0-9a-f]{64}$'"
ACTIVE = "('EXPERIMENTAL_PAPER','PAUSED','QUALIFIED_PAPER')"


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute(f"""CREATE TABLE office_strategy_versions (
        strategy_id TEXT NOT NULL CHECK(strategy_id ~ '^[A-Za-z0-9._:-]{{1,80}}$'),
        version TEXT NOT NULL CHECK(version ~ '^[A-Za-z0-9._:-]{{1,80}}$'),
        source_repository TEXT NOT NULL CHECK(source_repository IN ('app','research')),
        evidence_tier TEXT NOT NULL CHECK(evidence_tier IN ('NONE','DEVELOPMENT','HOLDOUT','FORWARD')),
        original_status TEXT NOT NULL CHECK(length(original_status) BETWEEN 1 AND 200),
        admissible BOOLEAN NOT NULL,
        execution_kind TEXT NOT NULL CHECK(execution_kind IN ('NONE','NATIVE','EXTERNAL_LOCAL')),
        risk_group TEXT NOT NULL CHECK(risk_group ~ '^[A-Za-z0-9._:-]{{1,80}}$'),
        record JSONB NOT NULL,
        record_sha256 TEXT NOT NULL CHECK(record_sha256 {SHA}),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(strategy_id, version))""")
    op.execute("""CREATE TABLE office_books (
        book_id TEXT PRIMARY KEY CHECK(book_id ~ '^[a-z0-9_-]{1,40}$'),
        starting_equity NUMERIC(14,2) NOT NULL CHECK(starting_equity > 0),
        max_active_deployments INT NOT NULL CHECK(max_active_deployments BETWEEN 1 AND 20),
        limits JSONB NOT NULL,
        cost_model JSONB NOT NULL,
        policy_version TEXT NOT NULL,
        simulated BOOLEAN NOT NULL DEFAULT true CHECK(simulated),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute(f"""CREATE TABLE office_admission_decisions (
        decision_id UUID PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        version TEXT NOT NULL,
        book_id TEXT REFERENCES office_books(book_id),
        policy_version TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK(outcome IN ('ADMITTED','BLOCKED')),
        checks JSONB NOT NULL,
        input_sha256 TEXT NOT NULL CHECK(input_sha256 {SHA}),
        reviewer_run_id UUID REFERENCES office_runs(run_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY(strategy_id, version) REFERENCES office_strategy_versions(strategy_id, version),
        CONSTRAINT office_admission_needs_reviewer_and_book
            CHECK(outcome <> 'ADMITTED' OR (reviewer_run_id IS NOT NULL AND book_id IS NOT NULL)))""")
    op.execute("CREATE UNIQUE INDEX office_admission_decisions_once ON office_admission_decisions"
               "(strategy_id, version, COALESCE(book_id, ''), input_sha256)")
    op.execute(f"""CREATE TABLE office_deployments (
        deployment_id UUID PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        version TEXT NOT NULL,
        book_id TEXT NOT NULL REFERENCES office_books(book_id),
        risk_group TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('BLOCKED','EXPERIMENTAL_PAPER','PAUSED','RETIRED','QUALIFIED_PAPER')),
        execution_kind TEXT NOT NULL CHECK(execution_kind IN ('NATIVE','EXTERNAL_LOCAL')),
        forward_protocol JSONB NOT NULL,
        admission_decision_id UUID NOT NULL REFERENCES office_admission_decisions(decision_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY(strategy_id, version) REFERENCES office_strategy_versions(strategy_id, version),
        UNIQUE(strategy_id, version, book_id))""")
    # One strategy, and one risk group, can be active in a book once: a second version or a near-copy waits.
    op.execute(f"CREATE UNIQUE INDEX office_deployments_one_active_strategy ON office_deployments"
               f"(book_id, strategy_id) WHERE state IN {ACTIVE}")
    op.execute(f"CREATE UNIQUE INDEX office_deployments_one_active_risk_group ON office_deployments"
               f"(book_id, risk_group) WHERE state IN {ACTIVE}")
    op.execute("""CREATE FUNCTION reject_office_strategy_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Office strategy records are written once' USING ERRCODE='55000'; END; $$""")
    for table in ("office_strategy_versions", "office_books", "office_admission_decisions"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_office_strategy_change()")
        op.execute(f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION reject_office_strategy_change()")
    op.execute("CREATE TRIGGER office_deployments_no_delete BEFORE DELETE ON office_deployments "
               "FOR EACH ROW EXECUTE FUNCTION reject_office_strategy_change()")
    op.execute("CREATE TRIGGER office_deployments_no_truncate BEFORE TRUNCATE ON office_deployments "
               "FOR EACH STATEMENT EXECUTE FUNCTION reject_office_strategy_change()")
    # A deployment needs an ADMITTED decision for exactly that version and book, and the version must be
    # admissible. After that only its state may change.
    op.execute("""CREATE FUNCTION check_office_deployment() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF (NEW.deployment_id, NEW.strategy_id, NEW.version, NEW.book_id, NEW.risk_group,
                    NEW.execution_kind, NEW.forward_protocol, NEW.admission_decision_id, NEW.created_at)
                   IS DISTINCT FROM
                   (OLD.deployment_id, OLD.strategy_id, OLD.version, OLD.book_id, OLD.risk_group,
                    OLD.execution_kind, OLD.forward_protocol, OLD.admission_decision_id, OLD.created_at) THEN
                    RAISE EXCEPTION 'Only the state of an office deployment may change' USING ERRCODE='55000';
                END IF;
                RETURN NEW;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM office_admission_decisions d
                           JOIN office_strategy_versions s ON s.strategy_id = d.strategy_id AND s.version = d.version
                           WHERE d.decision_id = NEW.admission_decision_id AND d.outcome = 'ADMITTED'
                             AND d.strategy_id = NEW.strategy_id AND d.version = NEW.version
                             AND d.book_id = NEW.book_id AND s.admissible
                             AND s.risk_group = NEW.risk_group AND s.execution_kind = NEW.execution_kind) THEN
                RAISE EXCEPTION 'An office deployment needs its own ADMITTED decision' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER office_deployments_checked BEFORE INSERT OR UPDATE ON office_deployments "
               "FOR EACH ROW EXECUTE FUNCTION check_office_deployment()")


def downgrade():
    raise RuntimeError("Preserve the office strategy inventory and decisions; use a reviewed forward migration")
