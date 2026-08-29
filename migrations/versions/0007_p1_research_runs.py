"""add p1 research runs

Revision ID: 0007_p1_research_runs
Revises: 0006_option_surface_snapshots
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_p1_research_runs"
down_revision = "0006_option_surface_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "p1_research_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("panel_start", sa.Date()),
        sa.Column("panel_end", sa.Date()),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("option_surface_coverage", sa.Float(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
    )
    op.create_index("p1_research_runs_created_idx", "p1_research_runs",
                    [sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("p1_research_runs_created_idx", table_name="p1_research_runs")
    op.drop_table("p1_research_runs")
