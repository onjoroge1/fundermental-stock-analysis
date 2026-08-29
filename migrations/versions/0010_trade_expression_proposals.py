"""add trade expression proposals

Revision ID: 0010_trade_expression_proposals
Revises: 0009_portfolio_proposals
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_trade_expression_proposals"
down_revision = "0009_portfolio_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_expression_proposals",
        sa.Column("expression_id", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("portfolio_proposal_id", sa.Text(), nullable=True),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("expression", sa.Text(), nullable=False),
        sa.Column("strategy_type", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
    )
    op.create_index("trade_expression_ticker_created_idx",
                    "trade_expression_proposals",
                    ["ticker", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("trade_expression_ticker_created_idx",
                  table_name="trade_expression_proposals")
    op.drop_table("trade_expression_proposals")
