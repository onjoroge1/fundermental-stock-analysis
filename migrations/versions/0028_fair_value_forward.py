"""Append-only evidence kinds for the fair-value forward tracker (sector P/E rule)."""

from importlib import import_module
from alembic import op

revision = "0028_fair_value_forward"
down_revision = "0027_agent_outcome_checks"
branch_labels = None
depends_on = None
KINDS = (
    *import_module("migrations.versions.0027_agent_outcome_checks").KINDS,
    "FV_FORWARD_PROTOCOL_V1",
    "FV_FORWARD_DECISION_V1",
    "FV_FORWARD_OUTCOME_V1",
    "FV_FORWARD_CHECK_V1",
    "FV_FORWARD_EVALUATION_V1",
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
    raise RuntimeError("Preserve forward-tracking evidence; use a reviewed forward migration")
