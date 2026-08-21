"""Persist precomputed prediction-lab forecasts.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-20
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS prediction_forecasts (
            ticker TEXT NOT NULL,
            as_of DATE NOT NULL,
            model_version TEXT NOT NULL,
            generated_at TIMESTAMPTZ DEFAULT now(),
            status TEXT NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (ticker, as_of, model_version)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS prediction_forecasts")
