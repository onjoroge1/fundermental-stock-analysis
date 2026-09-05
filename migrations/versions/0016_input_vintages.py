"""Preserve observation vintages without inventing historical availability."""
from alembic import op

revision = "0016_input_vintages"
down_revision = "0015_forecast_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE consensus_snapshots ADD COLUMN period_basis TEXT NOT NULL DEFAULT 'legacy'")
    op.execute("""CREATE TABLE earnings_surprise_vintages (
        ticker TEXT NOT NULL, event_date DATE NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        content_hash TEXT NOT NULL, actual_eps DOUBLE PRECISION,
        estimated_eps DOUBLE PRECISION, surprise_pct DOUBLE PRECISION,
        PRIMARY KEY (ticker, event_date, content_hash))""")
    op.execute("""CREATE TABLE macro_series_vintages (
        series_id TEXT NOT NULL, observation_date DATE NOT NULL,
        available_at TIMESTAMPTZ NOT NULL, value DOUBLE PRECISION NOT NULL,
        source TEXT NOT NULL, PRIMARY KEY (series_id, observation_date, available_at))""")
    # Legacy current-value tables remain available for descriptive displays.
    # Their values are intentionally NOT backdated into the new PIT stores.


def downgrade():
    raise RuntimeError("Input vintages are audit records and must not be dropped automatically")
