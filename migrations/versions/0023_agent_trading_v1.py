"""Agent Trading v1: deterministic paper intents, fills, positions and marks."""
from alembic import op

revision = "0023_agent_trading_v1"
down_revision = "0022_admin_panel"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE operator_controls
        ADD COLUMN trading_mode TEXT NOT NULL DEFAULT 'RESEARCH'
        CHECK (trading_mode IN ('RESEARCH','PAPER'))""")
    op.execute("""CREATE TABLE agent_trade_intents (
        intent_id UUID PRIMARY KEY,
        decision_id UUID NOT NULL REFERENCES agent_lab_decisions(decision_id),
        ticker TEXT NOT NULL CHECK(ticker IN ('AAPL','MSFT','UBER','HIMS','VZ')),
        classification TEXT,
        desired_side TEXT NOT NULL CHECK(desired_side IN ('LONG','SHORT','FLAT')),
        action TEXT NOT NULL CHECK(action IN ('OPEN_LONG','OPEN_SHORT','CLOSE','HOLD','NO_TRADE')),
        status TEXT NOT NULL CHECK(status IN ('APPROVED','BLOCKED','NO_ACTION','SIMULATED')),
        market_date DATE,
        reference_price DOUBLE PRECISION,
        target_notional_usd DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(target_notional_usd >= 0),
        rationale TEXT NOT NULL,
        blockers JSONB NOT NULL DEFAULT '[]'::jsonb,
        risk_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(decision_id))""")
    op.execute("CREATE INDEX agent_trade_intents_ticker_created ON agent_trade_intents(ticker,created_at DESC)")
    op.execute("""CREATE TABLE agent_paper_positions (
        position_id UUID PRIMARY KEY,
        ticker TEXT NOT NULL CHECK(ticker IN ('AAPL','MSFT','UBER','HIMS','VZ')),
        side TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
        source_decision_id UUID NOT NULL REFERENCES agent_lab_decisions(decision_id),
        source_intent_id UUID NOT NULL REFERENCES agent_trade_intents(intent_id),
        entry_market_date DATE NOT NULL,
        entry_price DOUBLE PRECISION NOT NULL CHECK(entry_price > 0),
        paper_units DOUBLE PRECISION NOT NULL CHECK(paper_units > 0),
        entry_notional_usd DOUBLE PRECISION NOT NULL CHECK(entry_notional_usd > 0),
        entry_cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK(entry_cost_usd >= 0),
        status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','CLOSED')),
        exit_market_date DATE,
        exit_price DOUBLE PRECISION,
        exit_cost_usd DOUBLE PRECISION,
        realized_pnl_usd DOUBLE PRECISION,
        exit_reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("CREATE UNIQUE INDEX agent_paper_one_open_per_ticker ON agent_paper_positions(ticker) WHERE status='OPEN'")
    op.execute("""CREATE TABLE agent_paper_fills (
        fill_id UUID PRIMARY KEY,
        intent_id UUID NOT NULL REFERENCES agent_trade_intents(intent_id),
        position_id UUID NOT NULL REFERENCES agent_paper_positions(position_id),
        ticker TEXT NOT NULL,
        fill_kind TEXT NOT NULL CHECK(fill_kind IN ('OPEN','CLOSE')),
        side TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
        market_date DATE NOT NULL,
        price DOUBLE PRECISION NOT NULL CHECK(price > 0),
        paper_units DOUBLE PRECISION NOT NULL CHECK(paper_units > 0),
        notional_usd DOUBLE PRECISION NOT NULL CHECK(notional_usd > 0),
        cost_usd DOUBLE PRECISION NOT NULL CHECK(cost_usd >= 0),
        simulated BOOLEAN NOT NULL DEFAULT true CHECK(simulated),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    op.execute("""CREATE TABLE agent_paper_marks (
        position_id UUID NOT NULL REFERENCES agent_paper_positions(position_id),
        market_date DATE NOT NULL,
        price DOUBLE PRECISION NOT NULL CHECK(price > 0),
        unrealized_pnl_usd DOUBLE PRECISION NOT NULL,
        gross_notional_usd DOUBLE PRECISION NOT NULL CHECK(gross_notional_usd >= 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(position_id,market_date))""")


def downgrade():
    raise RuntimeError("Preserve paper-trading evidence; use a reviewed forward migration")
