"""add option surface snapshots

Revision ID: 0006_option_surface_snapshots
Revises: 0005_macro_series
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_option_surface_snapshots"
down_revision = "0005_macro_series"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "option_surface_snapshots",
        sa.Column("snapshot_id", sa.Text(), primary_key=True),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("near_expiration", sa.Date()),
        sa.Column("atm_iv", sa.Float()),
        sa.Column("iv_skew_25d", sa.Float()),
        sa.Column("term_slope", sa.Float()),
        sa.Column("expected_move_pct", sa.Float()),
        sa.Column("put_call_oi_ratio", sa.Float()),
        sa.Column("iv_percentile", sa.Float()),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("ticker", "as_of", "provider", name="uq_option_surface_ticker_asof_provider"),
    )
    op.create_index("option_surface_ticker_asof_idx", "option_surface_snapshots",
                    ["ticker", sa.text("as_of DESC")])


def downgrade() -> None:
    op.drop_index("option_surface_ticker_asof_idx", table_name="option_surface_snapshots")
    op.drop_table("option_surface_snapshots")
