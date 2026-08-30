"""add API control-plane jobs and research index

Revision ID: 0014_api_control_plane
Revises: 0013_forward_paper_v2

The DDL is intentionally IF-NOT-EXISTS because the authenticated PR32 enqueue
endpoint can bootstrap these two tables before Alembic is run. A later formal
`alembic upgrade head` therefore remains safe and records the revision.
"""
from alembic import op

revision = "0014_api_control_plane"
down_revision = "0013_forward_paper_v2"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE IF NOT EXISTS orchestration_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    ticker TEXT,
    status TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    last_error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    idempotency_key TEXT NOT NULL UNIQUE,
    lease_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS orchestration_jobs_queue_idx
    ON orchestration_jobs (status, created_at);
CREATE TABLE IF NOT EXISTS stock_research_index (
    ticker TEXT PRIMARY KEY,
    as_of DATE NOT NULL,
    snapshot JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stock_research_index_updated_idx
    ON stock_research_index (updated_at DESC);
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS stock_research_index")
    op.execute("DROP TABLE IF EXISTS orchestration_jobs")
