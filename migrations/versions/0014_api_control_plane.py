"""add API control-plane jobs and research index

Revision ID: 0014_api_control_plane
Revises: 0013_forward_paper_v2
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_api_control_plane"
down_revision = "0013_forward_paper_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orchestration_jobs",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="orchestration_jobs_idempotency_uq"),
    )
    op.create_index(
        "orchestration_jobs_queue_idx", "orchestration_jobs",
        ["status", "created_at"],
    )
    op.create_table(
        "stock_research_index",
        sa.Column("ticker", sa.Text(), primary_key=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "stock_research_index_updated_idx", "stock_research_index",
        [sa.text("updated_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("stock_research_index_updated_idx", table_name="stock_research_index")
    op.drop_table("stock_research_index")
    op.drop_index("orchestration_jobs_queue_idx", table_name="orchestration_jobs")
    op.drop_table("orchestration_jobs")
