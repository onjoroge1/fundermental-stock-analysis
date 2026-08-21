"""Persist current strategy screens and an isolated strategy paper ledger.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-21
"""
from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS sm_strategy_screens (
            screen_id TEXT PRIMARY KEY,
            strategy_lab_run_id TEXT NOT NULL,
            source_backtest_run_id TEXT NOT NULL,
            as_of DATE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            status TEXT NOT NULL,
            result JSONB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sm_strategy_paper_positions (
            position_id BIGSERIAL PRIMARY KEY,
            policy TEXT NOT NULL,
            ticker TEXT NOT NULL,
            screen_id TEXT NOT NULL,
            target_weight DOUBLE PRECISION NOT NULL,
            entry_date DATE NOT NULL,
            entry_price DOUBLE PRECISION NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            exit_date DATE,
            exit_price DOUBLE PRECISION,
            exit_reason TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS sm_strategy_paper_open_idx
            ON sm_strategy_paper_positions (policy, ticker)
            WHERE status = 'open';
        CREATE TABLE IF NOT EXISTS sm_strategy_paper_nav (
            date DATE NOT NULL,
            policy TEXT NOT NULL,
            return_pct DOUBLE PRECISION,
            n_positions INT NOT NULL,
            details JSONB NOT NULL,
            PRIMARY KEY (date, policy)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sm_strategy_paper_nav")
    op.execute("DROP TABLE IF EXISTS sm_strategy_paper_positions")
    op.execute("DROP TABLE IF EXISTS sm_strategy_screens")
