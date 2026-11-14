"""Trading Office experimental paper ledger: opportunities, simulated orders and fills, positions, marks.

Additive only. It creates five new ``office_`` tables and alters no existing table. Everything here is a
simulation: the tables cannot hold a broker fill, and a fill can never be dated on or before its signal.
"""
from alembic import op

revision = "0033_office_paper"
down_revision = "0032_office_strategies"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    # Every signal seen, taken or not. A scan that finds nothing records one NO_OPPORTUNITY row (ticker '').
    op.execute("""CREATE TABLE office_opportunities (
        opportunity_id UUID PRIMARY KEY,
        deployment_id UUID NOT NULL REFERENCES office_deployments(deployment_id),
        book_id TEXT NOT NULL REFERENCES office_books(book_id),
        ticker TEXT NOT NULL CHECK(ticker ~ '^[A-Z0-9.-]{0,12}$'),
        side TEXT CHECK(side IN ('LONG','SHORT')),
        signal_session DATE NOT NULL,
        signal_rank INT,
        holding_sessions INT CHECK(holding_sessions BETWEEN 1 AND 260),
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        entry_due_at TIMESTAMPTZ,
        status TEXT NOT NULL CHECK(status IN ('ACCEPTED','REJECTED','MISSED','NO_OPPORTUNITY')),
        reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
        sizing JSONB NOT NULL DEFAULT '{}'::jsonb,
        UNIQUE(deployment_id, ticker, signal_session),
        CONSTRAINT office_opportunities_none_has_no_ticker CHECK((status = 'NO_OPPORTUNITY') = (ticker = '')),
        CONSTRAINT office_opportunities_signal_fields CHECK(status = 'NO_OPPORTUNITY' OR
            (side IS NOT NULL AND holding_sessions IS NOT NULL AND entry_due_at IS NOT NULL)),
        CONSTRAINT office_opportunities_accepted_in_time CHECK(status <> 'ACCEPTED' OR recorded_at <= entry_due_at))""")
    op.execute("""CREATE TABLE office_orders (
        order_id UUID PRIMARY KEY,
        opportunity_id UUID NOT NULL UNIQUE REFERENCES office_opportunities(opportunity_id),
        deployment_id UUID NOT NULL REFERENCES office_deployments(deployment_id),
        book_id TEXT NOT NULL REFERENCES office_books(book_id),
        ticker TEXT NOT NULL,
        side TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
        shares NUMERIC(18,4) NOT NULL CHECK(shares > 0),
        reference_price NUMERIC(18,6) NOT NULL CHECK(reference_price > 0),
        reserved_usd NUMERIC(14,2) NOT NULL CHECK(reserved_usd > 0),
        signal_session DATE NOT NULL,
        holding_sessions INT NOT NULL,
        state TEXT NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','FILLED','EXPIRED')),
        reason_code TEXT,
        decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        simulated BOOLEAN NOT NULL DEFAULT true CHECK(simulated))""")
    op.execute("""CREATE TABLE office_fills (
        fill_id UUID PRIMARY KEY,
        order_id UUID NOT NULL REFERENCES office_orders(order_id),
        leg TEXT NOT NULL CHECK(leg IN ('ENTRY','EXIT')),
        fill_session DATE NOT NULL,
        price NUMERIC(18,6) NOT NULL CHECK(price > 0),
        shares NUMERIC(18,4) NOT NULL CHECK(shares > 0),
        price_source TEXT NOT NULL CHECK(price_source IN ('BAR_OPEN','BAR_CLOSE','EXTERNAL_MODELLED')),
        cost_usd NUMERIC(14,4) NOT NULL CHECK(cost_usd >= 0),
        costs JSONB NOT NULL,
        simulated BOOLEAN NOT NULL DEFAULT true CHECK(simulated),
        broker_fill BOOLEAN NOT NULL DEFAULT false CHECK(NOT broker_fill),
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(order_id, leg))""")
    op.execute("""CREATE TABLE office_positions (
        position_id UUID PRIMARY KEY,
        order_id UUID NOT NULL UNIQUE REFERENCES office_orders(order_id),
        deployment_id UUID NOT NULL REFERENCES office_deployments(deployment_id),
        book_id TEXT NOT NULL REFERENCES office_books(book_id),
        ticker TEXT NOT NULL,
        side TEXT NOT NULL CHECK(side IN ('LONG','SHORT')),
        shares NUMERIC(18,4) NOT NULL CHECK(shares > 0),
        entry_session DATE NOT NULL,
        entry_price NUMERIC(18,6) NOT NULL,
        holding_sessions INT NOT NULL,
        state TEXT NOT NULL DEFAULT 'OPEN' CHECK(state IN ('OPEN','CLOSED')),
        exit_session DATE,
        exit_price NUMERIC(18,6),
        costs_usd NUMERIC(14,4) NOT NULL DEFAULT 0,
        realized_pnl_usd NUMERIC(14,4),
        CONSTRAINT office_positions_closed_fields CHECK((state = 'CLOSED') =
            (exit_session IS NOT NULL AND exit_price IS NOT NULL AND realized_pnl_usd IS NOT NULL)),
        CONSTRAINT office_positions_exit_after_entry CHECK(exit_session IS NULL OR exit_session >= entry_session))""")
    # One open position per ticker per book, across all strategies: no duplicate allocation.
    op.execute("CREATE UNIQUE INDEX office_positions_one_open_per_ticker ON office_positions(book_id, ticker) "
               "WHERE state = 'OPEN'")
    op.execute("CREATE UNIQUE INDEX office_orders_one_pending_per_ticker ON office_orders(book_id, ticker) "
               "WHERE state = 'PENDING'")
    op.execute("""CREATE TABLE office_marks (
        book_id TEXT NOT NULL REFERENCES office_books(book_id),
        session DATE NOT NULL,
        equity_usd NUMERIC(14,2) NOT NULL,
        realized_usd NUMERIC(14,2) NOT NULL,
        unrealized_usd NUMERIC(14,2) NOT NULL,
        gross_exposure_usd NUMERIC(14,2) NOT NULL,
        open_positions INT NOT NULL,
        detail JSONB NOT NULL DEFAULT '{}'::jsonb,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(book_id, session))""")
    op.execute("""CREATE FUNCTION reject_office_paper_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Office paper records are written once' USING ERRCODE='55000'; END; $$""")
    for table in ("office_opportunities", "office_fills", "office_marks"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_office_paper_change()")
    for table in ("office_orders", "office_positions"):
        op.execute(f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_office_paper_change()")
    for table in ("office_opportunities", "office_orders", "office_fills", "office_positions", "office_marks"):
        op.execute(f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION reject_office_paper_change()")
    # An order needs an ACCEPTED opportunity, decided before its entry deadline. Afterwards only its state moves.
    op.execute("""CREATE FUNCTION check_office_order() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF (NEW.order_id, NEW.opportunity_id, NEW.deployment_id, NEW.book_id, NEW.ticker, NEW.side,
                    NEW.shares, NEW.reference_price, NEW.reserved_usd, NEW.signal_session, NEW.decided_at)
                   IS DISTINCT FROM
                   (OLD.order_id, OLD.opportunity_id, OLD.deployment_id, OLD.book_id, OLD.ticker, OLD.side,
                    OLD.shares, OLD.reference_price, OLD.reserved_usd, OLD.signal_session, OLD.decided_at)
                   OR OLD.state <> 'PENDING' THEN
                    RAISE EXCEPTION 'Only a pending office order may change, and only its state' USING ERRCODE='55000';
                END IF;
                RETURN NEW;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM office_opportunities o
                           WHERE o.opportunity_id = NEW.opportunity_id AND o.status = 'ACCEPTED'
                             AND o.deployment_id = NEW.deployment_id AND o.ticker = NEW.ticker
                             AND o.side = NEW.side AND o.signal_session = NEW.signal_session
                             AND NEW.decided_at <= o.entry_due_at) THEN
                RAISE EXCEPTION 'An office order needs an accepted opportunity decided before its entry deadline'
                    USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER office_orders_checked BEFORE INSERT OR UPDATE ON office_orders "
               "FOR EACH ROW EXECUTE FUNCTION check_office_order()")
    # A simulated entry can only be dated after the session whose data produced the signal.
    op.execute("""CREATE FUNCTION check_office_fill() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM office_orders r
                           WHERE r.order_id = NEW.order_id AND NEW.fill_session > r.signal_session) THEN
                RAISE EXCEPTION 'An office fill cannot be dated on or before its signal session' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER office_fills_checked BEFORE INSERT ON office_fills "
               "FOR EACH ROW EXECUTE FUNCTION check_office_fill()")
    op.execute("""CREATE FUNCTION check_office_position() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.state = 'CLOSED' OR (NEW.position_id, NEW.order_id, NEW.deployment_id, NEW.book_id, NEW.ticker,
                    NEW.side, NEW.shares, NEW.entry_session, NEW.entry_price, NEW.holding_sessions)
               IS DISTINCT FROM (OLD.position_id, OLD.order_id, OLD.deployment_id, OLD.book_id, OLD.ticker,
                    OLD.side, OLD.shares, OLD.entry_session, OLD.entry_price, OLD.holding_sessions) THEN
                RAISE EXCEPTION 'An office position can only be closed, once' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER office_positions_checked BEFORE UPDATE ON office_positions "
               "FOR EACH ROW EXECUTE FUNCTION check_office_position()")


def downgrade():
    raise RuntimeError("Preserve the office paper ledger; use a reviewed forward migration")
