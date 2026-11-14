"""Trading Office research records: events, memos, reviews and briefings.

Additive only. It creates four new ``office_`` tables and alters no existing table. Every row is written
once and never changed: a revised memo is a new version, a second look is a new review.
"""
from alembic import op

revision = "0031_office_research"
down_revision = "0030_office_budget"
branch_labels = None
depends_on = None

TABLES = ("office_events", "office_memos", "office_reviews", "office_briefings")
SHA = "~ '^[0-9a-f]{64}$'"


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute(f"""CREATE TABLE office_events (
        event_id UUID PRIMARY KEY,
        source TEXT NOT NULL CHECK(source IN ('company_event_snapshots','filings')),
        source_key TEXT NOT NULL CHECK(length(source_key) BETWEEN 1 AND 300),
        content_sha256 TEXT NOT NULL CHECK(content_sha256 {SHA}),
        ticker TEXT NOT NULL CHECK(ticker ~ '^[A-Z0-9.-]{{1,12}}$'),
        event_type TEXT NOT NULL CHECK(event_type ~ '^[A-Z_]{{1,24}}$'),
        event_date DATE NOT NULL,
        detail JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        first_observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(source, source_key, content_sha256))""")
    op.execute("CREATE INDEX office_events_observed ON office_events(first_observed_at)")
    op.execute(f"""CREATE TABLE office_memos (
        memo_id UUID NOT NULL,
        version INT NOT NULL CHECK(version BETWEEN 1 AND 2),
        event_id UUID NOT NULL REFERENCES office_events(event_id),
        ticker TEXT NOT NULL,
        body JSONB NOT NULL,
        input_sha256 TEXT NOT NULL CHECK(input_sha256 {SHA}),
        author_run_id UUID NOT NULL REFERENCES office_runs(run_id),
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        expires_on DATE NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(memo_id, version),
        UNIQUE(event_id, version))""")
    op.execute("""CREATE TABLE office_reviews (
        review_id UUID PRIMARY KEY,
        memo_id UUID NOT NULL,
        memo_version INT NOT NULL,
        verdict TEXT NOT NULL CHECK(verdict IN ('PASS','REVISION_REQUESTED','BLOCKED')),
        findings JSONB NOT NULL DEFAULT '[]'::jsonb,
        reviewer_run_id UUID NOT NULL REFERENCES office_runs(run_id),
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY(memo_id, memo_version) REFERENCES office_memos(memo_id, version),
        UNIQUE(memo_id, memo_version),
        CONSTRAINT office_reviews_one_revision_round
            CHECK(NOT (memo_version >= 2 AND verdict = 'REVISION_REQUESTED')))""")
    op.execute("CREATE INDEX office_reviews_created ON office_reviews(created_at)")
    op.execute("""CREATE TABLE office_briefings (
        briefing_id UUID PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('MORNING','WEEKLY')),
        briefing_date DATE NOT NULL,
        content JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(kind, briefing_date))""")
    op.execute("""CREATE FUNCTION reject_office_research_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Office research records are written once' USING ERRCODE='55000'; END; $$""")
    for table in TABLES:
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_office_research_change()")
        op.execute(f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION reject_office_research_change()")
    # A memo cannot be reviewed by the run, or the task, that wrote any version of it.
    op.execute("""CREATE FUNCTION reject_office_review_by_author() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM office_memos m
                       JOIN office_runs a ON a.run_id = m.author_run_id
                       JOIN office_runs r ON r.run_id = NEW.reviewer_run_id
                       WHERE m.memo_id = NEW.memo_id AND (a.run_id = r.run_id OR a.task_id = r.task_id)) THEN
                RAISE EXCEPTION 'A memo cannot be reviewed by its author' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER office_reviews_independent BEFORE INSERT ON office_reviews "
               "FOR EACH ROW EXECUTE FUNCTION reject_office_review_by_author()")


def downgrade():
    raise RuntimeError("Preserve office research records; use a reviewed forward migration")
