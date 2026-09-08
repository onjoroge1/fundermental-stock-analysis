"""Durable evidence for licensed point-in-time estimate imports."""
from alembic import op

revision = '0018_consensus_archives'
down_revision = '0017_consensus_vintages'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE consensus_archive_imports (
        archive_sha256 TEXT PRIMARY KEY,
        imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        archive JSONB NOT NULL, row_count INTEGER NOT NULL CHECK(row_count > 0))''')


def downgrade():
    raise RuntimeError('Consensus archives are audit evidence; preserve them')
