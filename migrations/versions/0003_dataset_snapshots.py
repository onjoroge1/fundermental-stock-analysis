"""Add append-only point-in-time dataset manifests.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-21
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS dataset_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            dataset TEXT NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            content_hash TEXT NOT NULL,
            row_count INT NOT NULL,
            min_record_date DATE,
            max_record_date DATE,
            status TEXT NOT NULL,
            reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
            metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
            payload JSONB,
            UNIQUE (ticker, dataset, content_hash)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS dataset_snapshots_latest_idx
        ON dataset_snapshots (ticker, dataset, observed_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dataset_snapshots")
