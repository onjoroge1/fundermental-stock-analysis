"""Append-only prospective shadow forecasts, outcomes and candidate weights."""

from importlib import import_module
from alembic import op

revision = "0025_agent_shadow"
down_revision = "0024_agent_universe_learning"
branch_labels = None
depends_on = None
KINDS = (
    *import_module("migrations.versions.0023_agent_intelligence_evidence").KINDS,
    "AGENT_BANDIT_STATE_V2",
    "AGENT_REWARD_V3",
    "AGENT_OUTCOME_EXCLUSION_V1",
    "AGENT_SHADOW_SNAPSHOT_V1",
    "AGENT_SHADOW_OUTCOME_V1",
    "AGENT_SHADOW_WEIGHTS_V1",
    "AGENT_SHADOW_CHECK_V1",
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
    raise RuntimeError("Preserve shadow evidence; use a reviewed forward migration")
