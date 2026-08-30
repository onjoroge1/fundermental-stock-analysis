"""add strategy lab v2 runs

Revision ID: 0012_strategy_lab_v2
Revises: 0011_company_events
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_strategy_lab_v2"
down_revision = "0011_company_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_lab_v2_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("panel_hash", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "strategy_lab_v2_latest_idx", "strategy_lab_v2_runs",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("strategy_lab_v2_latest_idx", table_name="strategy_lab_v2_runs")
    op.drop_table("strategy_lab_v2_runs")
