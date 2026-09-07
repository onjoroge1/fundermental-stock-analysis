"""Preserve provider identity and intraday current-consensus observations."""
from alembic import op

revision = '0017_consensus_vintages'
down_revision = '0016_input_vintages'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE consensus_vintages (
        ticker TEXT NOT NULL, source TEXT NOT NULL,
        observed_at TIMESTAMPTZ NOT NULL,
        period_type TEXT NOT NULL CHECK (period_type IN ('annual','quarter')),
        forecast_period_end DATE NOT NULL, payload JSONB NOT NULL,
        PRIMARY KEY (ticker,source,observed_at,period_type,forecast_period_end))''')
    # Legacy dates and values remain intact; their provider/time is unknown.
    # Do not invent precise historical timestamps by copying them here.


def downgrade():
    raise RuntimeError('Consensus vintages are audit records; preserve them')
