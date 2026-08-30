"""add forward paper v2 cohorts and marks

Revision ID: 0013_forward_paper_v2
Revises: 0012_strategy_lab_v2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013_forward_paper_v2"
down_revision = "0012_strategy_lab_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forward_paper_v2_cohorts",
        sa.Column("cohort_id", sa.Text(), primary_key=True),
        sa.Column("lab_run_id", sa.Text(), nullable=False),
        sa.Column("policy_name", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("entry_market_date", sa.Date(), nullable=False),
        sa.Column("contract_hash", sa.Text(), nullable=False),
        sa.Column("contract", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("lab_run_id", "policy_name", "mode", "contract_hash",
                            name="forward_paper_v2_cohort_identity"),
    )
    op.create_index(
        "forward_paper_v2_cohorts_latest_idx", "forward_paper_v2_cohorts",
        ["policy_name", "mode", sa.text("created_at DESC")],
    )
    op.create_table(
        "forward_paper_v2_marks",
        sa.Column("cohort_id", sa.Text(), nullable=False),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("mark", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("cohort_id", "market_date",
                                name="forward_paper_v2_marks_pk"),
        sa.ForeignKeyConstraint(["cohort_id"], ["forward_paper_v2_cohorts.cohort_id"],
                                ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("forward_paper_v2_marks")
    op.drop_index("forward_paper_v2_cohorts_latest_idx",
                  table_name="forward_paper_v2_cohorts")
    op.drop_table("forward_paper_v2_cohorts")
