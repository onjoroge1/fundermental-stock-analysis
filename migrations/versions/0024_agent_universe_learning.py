"""Expand operational coverage and isolate execution-backed learning history."""

from alembic import op
from importlib import import_module

revision = "0024_agent_universe_learning"
down_revision = "0023_agent_intelligence_evidence"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE operator_pilot_items DROP CONSTRAINT operator_pilot_items_ticker_check"
    )
    # Optional paper tables may not exist until the owner enables PAPER.
    op.execute("""DO $$ BEGIN
        IF to_regclass('agent_trade_intents') IS NOT NULL THEN
            ALTER TABLE agent_trade_intents DROP CONSTRAINT IF EXISTS agent_trade_intents_ticker_check;
            ALTER TABLE agent_trade_intents DROP CONSTRAINT agent_trade_intents_status_check;
            ALTER TABLE agent_trade_intents ADD CONSTRAINT agent_trade_intents_status_check
                CHECK(status IN ('APPROVED','BLOCKED','NO_ACTION','SIMULATED','PENDING'));
        END IF;
        IF to_regclass('agent_paper_positions') IS NOT NULL THEN
            ALTER TABLE agent_paper_positions DROP CONSTRAINT IF EXISTS agent_paper_positions_ticker_check;
        END IF;
    END $$""")
    previous = import_module("migrations.versions.0023_agent_intelligence_evidence")
    kinds = (
        *previous.KINDS,
        "AGENT_BANDIT_STATE_V2",
        "AGENT_REWARD_V3",
        "AGENT_OUTCOME_EXCLUSION_V1",
    )
    allowed = ",".join(f"'{kind}'" for kind in kinds)
    op.execute(
        "ALTER TABLE research_evidence_records DROP CONSTRAINT research_evidence_records_kind_check"
    )
    op.execute(
        f"ALTER TABLE research_evidence_records ADD CONSTRAINT research_evidence_records_kind_check CHECK(kind IN ({allowed}))"
    )


def downgrade():
    raise RuntimeError(
        "Preserve agent learning and execution evidence; use a reviewed forward migration"
    )
