"""Separate historical setup candidates and prospective frozen setup evidence."""

from importlib import import_module
from alembic import op

revision = "0026_technical_setup_shadow"
down_revision = "0025_agent_shadow"
branch_labels = None
depends_on = None
KINDS = (
    *import_module("migrations.versions.0025_agent_shadow").KINDS,
    "TECH_SETUP_RUN_V1",
    "TECH_SETUP_SNAPSHOT_V1",
    "TECH_SETUP_OUTCOME_V1",
    "TECH_SETUP_INPUT_V1",
    "TECH_SETUP_CHECK_V1",
)


def upgrade():
    allowed = ",".join("'" + k + "'" for k in KINDS)
    op.execute(
        "ALTER TABLE research_evidence_records DROP CONSTRAINT research_evidence_records_kind_check"
    )
    op.execute(
        "ALTER TABLE research_evidence_records ADD CONSTRAINT research_evidence_records_kind_check CHECK(kind IN ("
        + allowed
        + "))"
    )

    op.execute(
        "CREATE INDEX technical_setup_attempt_idx ON research_evidence_records "
        "((payload->>'snapshot_key'),recorded_at DESC) WHERE kind='TECH_SETUP_CHECK_V1'"
    )


def downgrade():
    raise RuntimeError(
        "Preserve technical setup evidence; use a reviewed forward migration"
    )
