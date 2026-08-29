"""add alpha shadow runs

Revision ID: 0004_alpha_shadow_runs
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_alpha_shadow_runs"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alpha_shadow_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("panel_start", sa.Date()),
        sa.Column("panel_end", sa.Date()),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("ticker_count", sa.Integer(), nullable=False),
        sa.Column("expectations_coverage", sa.Float(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
    )
    op.create_index("alpha_shadow_runs_created_idx", "alpha_shadow_runs",
                    [sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("alpha_shadow_runs_created_idx", table_name="alpha_shadow_runs")
    op.drop_table("alpha_shadow_runs")
