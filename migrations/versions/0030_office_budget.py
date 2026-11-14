"""Trading Office spend ledger: one reservation per paid model call.

Additive only. It creates one new ``office_`` table and alters no existing table.
"""
from alembic import op

revision = "0030_office_budget"
down_revision = "0029_office_tasks"
branch_labels = None
depends_on = None

STATES = ("RESERVED", "RECONCILED", "RELEASED", "UNRESOLVED")


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    states = ",".join(f"'{s}'" for s in STATES)
    op.execute(f"""CREATE TABLE office_budget_reservations (
        reservation_id UUID PRIMARY KEY,
        run_id UUID NOT NULL REFERENCES office_runs(run_id),
        call_number INT NOT NULL CHECK(call_number > 0),
        provider TEXT NOT NULL CHECK(provider IN ('fake','anthropic','openai')),
        model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 120),
        reserved_usd NUMERIC(12,6) NOT NULL CHECK(reserved_usd > 0 AND reserved_usd <= 100),
        actual_usd NUMERIC(12,6) CHECK(actual_usd IS NULL OR actual_usd >= 0),
        state TEXT NOT NULL DEFAULT 'RESERVED' CHECK(state IN ({states})),
        usage JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        settled_at TIMESTAMPTZ,
        UNIQUE(run_id, call_number),
        CONSTRAINT office_budget_reconciled_has_cost CHECK((state = 'RECONCILED') = (actual_usd IS NOT NULL)),
        CONSTRAINT office_budget_settled_time CHECK((state = 'RESERVED') = (settled_at IS NULL)))""")
    op.execute("CREATE INDEX office_budget_reservations_created ON office_budget_reservations(created_at)")
    op.execute("""CREATE FUNCTION reject_office_budget_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Office budget reservations are never deleted' USING ERRCODE='55000'; END; $$""")
    op.execute("CREATE TRIGGER office_budget_reservations_no_delete BEFORE DELETE "
               "ON office_budget_reservations FOR EACH ROW EXECUTE FUNCTION reject_office_budget_delete()")
    op.execute("CREATE TRIGGER office_budget_reservations_no_truncate BEFORE TRUNCATE "
               "ON office_budget_reservations FOR EACH STATEMENT EXECUTE FUNCTION reject_office_budget_delete()")


def downgrade():
    raise RuntimeError("Preserve the office spend ledger; use a reviewed forward migration")
