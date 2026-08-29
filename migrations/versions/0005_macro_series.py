"""add macro series

Revision ID: 0005_macro_series
Revises: 0004_alpha_shadow_runs
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_macro_series"
down_revision = "0004_alpha_shadow_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "macro_series",
        sa.Column("series_id", sa.Text(), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("available_at", sa.Date(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("series_id", "observation_date"),
    )
    op.create_index("macro_series_available_idx", "macro_series",
                    ["series_id", "available_at"])


def downgrade() -> None:
    op.drop_index("macro_series_available_idx", table_name="macro_series")
    op.drop_table("macro_series")
