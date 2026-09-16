"""Move legacy monitoring table creation out of the read-only research path."""
from alembic import op

revision = "0021_monitoring_storage"
down_revision = "0020_research_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS sm_invalidation_events (
        ticker TEXT NOT NULL, rule_id TEXT NOT NULL, report_id TEXT NOT NULL,
        observed DOUBLE PRECISION, threshold DOUBLE PRECISION, op TEXT,
        description TEXT, triggered_at DATE DEFAULT CURRENT_DATE,
        PRIMARY KEY (ticker, rule_id, report_id))""")


def downgrade():
    raise RuntimeError("Preserve existing monitoring evidence")
