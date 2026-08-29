"""add alpha probability outcomes

Revision ID: 0008_alpha_probability_outcomes
Revises: 0007_p1_research_runs
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_alpha_probability_outcomes"
down_revision = "0007_p1_research_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alpha_probability_outcomes",
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("prob_outperform", sa.Float(), nullable=False),
        sa.Column("actual_excess_return_pct", sa.Float(), nullable=False),
        sa.Column("actual_outperform", sa.Boolean(), nullable=False),
        sa.Column("regime", sa.Text()),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ticker", "as_of", "model_version", "horizon_days"),
    )
    op.create_index("alpha_probability_outcomes_horizon_idx",
                    "alpha_probability_outcomes", ["horizon_days", "regime"])


def downgrade() -> None:
    op.drop_index("alpha_probability_outcomes_horizon_idx",
                  table_name="alpha_probability_outcomes")
    op.drop_table("alpha_probability_outcomes")
