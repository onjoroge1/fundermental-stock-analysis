"""Persist walk-forward portfolio-strategy evaluations.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-21
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS sm_strategy_lab_runs (
            run_id TEXT PRIMARY KEY,
            source_backtest_run_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            config JSONB NOT NULL,
            status TEXT NOT NULL,
            result JSONB NOT NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sm_strategy_lab_runs")
