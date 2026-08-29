"""add portfolio proposals

Revision ID: 0009_portfolio_proposals
Revises: 0008_alpha_probability_outcomes
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009_portfolio_proposals"
down_revision = "0008_alpha_probability_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portfolio_proposals",
        sa.Column("proposal_id", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("gross_exposure", sa.Float(), nullable=False),
        sa.Column("net_exposure", sa.Float(), nullable=False),
        sa.Column("beta_exposure", sa.Float(), nullable=False),
        sa.Column("position_count", sa.Integer(), nullable=False),
        sa.Column("proposal", postgresql.JSONB(), nullable=False),
    )
    op.create_index("portfolio_proposals_created_idx", "portfolio_proposals",
                    [sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("portfolio_proposals_created_idx", table_name="portfolio_proposals")
    op.drop_table("portfolio_proposals")
