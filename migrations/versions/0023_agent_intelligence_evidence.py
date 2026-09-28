"""Allow the append-only evidence kinds used by Agent Intelligence v2."""
from alembic import op

revision = "0023_agent_intelligence_evidence"
down_revision = "0022_admin_panel"
branch_labels = None
depends_on = None


KINDS = (
    "RAW_SOURCE",
    "BALANCE_AUDIT",
    "CLAIM_AUDIT",
    "PROVIDER_DAILY",
    "RESEARCH_SNAPSHOT",
    "CYCLE_RESULT",
    "EXPERIMENT_PROTOCOL",
    "EXPERIMENT_FORECAST",
    "EXPERIMENT_OUTCOME",
    "AGENT_INTELLIGENCE_V2",
    "AGENT_INTELLIGENCE_V2_FAILURE",
    "AGENT_BANDIT_STATE_V1",
    "AGENT_REWARD_V2",
    "AGENT_OPTION_PAPER_V1",
    "AGENT_OPTION_PAPER_OUTCOME_V1",
)


def upgrade():
    allowed = ",".join(f"'{kind}'" for kind in KINDS)
    op.execute("ALTER TABLE research_evidence_records "
               "DROP CONSTRAINT research_evidence_records_kind_check")
    op.execute("ALTER TABLE research_evidence_records "
               "ADD CONSTRAINT research_evidence_records_kind_check "
               f"CHECK(kind IN ({allowed}))")


def downgrade():
    raise RuntimeError(
        "Preserve Agent Intelligence v2 evidence; use a reviewed forward migration"
    )
