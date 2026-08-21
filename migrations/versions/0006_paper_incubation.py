"""Add immutable forward paper cohorts and benchmark-relative marks.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-21
"""
from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS sm_strategy_paper_cohorts (
            cohort_id TEXT PRIMARY KEY,
            policy TEXT NOT NULL,
            screen_id TEXT NOT NULL,
            start_date DATE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            signature TEXT NOT NULL,
            turnover DOUBLE PRECISION NOT NULL,
            cost_bps DOUBLE PRECISION NOT NULL,
            cost_pct DOUBLE PRECISION NOT NULL,
            holdings JSONB NOT NULL,
            benchmark JSONB NOT NULL
        );
        CREATE INDEX IF NOT EXISTS sm_strategy_cohorts_policy_idx
            ON sm_strategy_paper_cohorts (policy, created_at DESC);
        CREATE TABLE IF NOT EXISTS sm_strategy_incubation_marks (
            cohort_id TEXT NOT NULL,
            date DATE NOT NULL,
            status TEXT NOT NULL,
            gross_return_pct DOUBLE PRECISION,
            net_return_pct DOUBLE PRECISION,
            benchmark_return_pct DOUBLE PRECISION,
            excess_return_pct DOUBLE PRECISION,
            coverage DOUBLE PRECISION NOT NULL,
            details JSONB NOT NULL,
            PRIMARY KEY (cohort_id, date)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sm_strategy_incubation_marks")
    op.execute("DROP TABLE IF EXISTS sm_strategy_paper_cohorts")
