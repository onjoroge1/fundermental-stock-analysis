"""add company event intelligence storage

Revision ID: 0011_company_events
Revises: 0010_trade_expression_proposals
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011_company_events"
down_revision = "0010_trade_expression_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_event_snapshots",
        sa.Column("event_snapshot_id", sa.Text(), primary_key=True),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "ticker", "event_type", "event_date", "source", "observed_on",
            name="company_event_snapshot_natural_key",
        ),
    )
    op.create_index(
        "company_event_ticker_date_idx", "company_event_snapshots",
        ["ticker", "event_date"],
    )
    op.create_index(
        "company_event_observed_idx", "company_event_snapshots",
        ["ticker", sa.text("observed_on DESC")],
    )

    op.create_table(
        "company_event_coverage",
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("coverage_status", sa.Text(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint(
            "ticker", "event_type", "observed_on", "source",
            name="company_event_coverage_pk",
        ),
    )
    op.create_index(
        "company_event_coverage_latest_idx", "company_event_coverage",
        ["ticker", "event_type", sa.text("observed_on DESC")],
    )


def downgrade() -> None:
    op.drop_index("company_event_coverage_latest_idx",
                  table_name="company_event_coverage")
    op.drop_table("company_event_coverage")
    op.drop_index("company_event_observed_idx",
                  table_name="company_event_snapshots")
    op.drop_index("company_event_ticker_date_idx",
                  table_name="company_event_snapshots")
    op.drop_table("company_event_snapshots")
