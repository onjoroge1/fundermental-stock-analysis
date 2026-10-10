"""Durable per-session retry evidence for blocked paper-learning outcomes."""

from importlib import import_module
from alembic import op

revision = "0027_agent_outcome_checks"
down_revision = "0026_technical_setup_shadow"
branch_labels = None
depends_on = None
KINDS = (
    *import_module("migrations.versions.0026_technical_setup_shadow").KINDS,
    "AGENT_OUTCOME_CHECK_V1",
)


def upgrade():
    allowed = ",".join(f"'{kind}'" for kind in KINDS)
    op.execute(
        "ALTER TABLE research_evidence_records DROP CONSTRAINT research_evidence_records_kind_check"
    )
    op.execute(
        "ALTER TABLE research_evidence_records ADD CONSTRAINT research_evidence_records_kind_check "
        f"CHECK(kind IN ({allowed}))"
    )


def downgrade():
    raise RuntimeError("Preserve learning evidence; use a reviewed forward migration")
