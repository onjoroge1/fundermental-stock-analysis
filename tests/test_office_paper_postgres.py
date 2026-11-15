"""Real PostgreSQL checks for the scanner and the simulated paper ledger.

The strategy, its rule and its prices are test records in a disposable schema. No real strategy has a rule
registered, and nothing here is a broker order or a broker fill.
"""
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from stock_machine.office import admission, config, inventory, paper
from stock_machine.office.paper import Signal
from tests.test_office_admission_postgres import fixture_row, signoff
from tests.test_office_research_postgres import pg, sql  # noqa: F401

T = date.today()
LIMITS = {"max_position_pct": 5, "max_gross_pct": 50, "max_positions": 10, "max_sector_pct": 20,
          "drawdown_review_pct": 10}
COSTS = {"bps_per_side": 10, "borrow_bps_per_year": 365}


def day(n):
    return T + timedelta(days=n)


def bar(pg, ticker, session, open_, close):
    sql(pg, "INSERT INTO prices_daily(ticker, date, open, close) VALUES (%s, %s, %s, %s)", (ticker, session, open_, close))


@pytest.fixture
def desk(pg, monkeypatch):
    """A test strategy admitted to a test book, with a rule whose signals each test sets."""
    monkeypatch.setenv("OFFICE_PAPER_ENABLED", "true")
    sql(pg, "ALTER TABLE prices_daily ADD COLUMN open DOUBLE PRECISION")
    sql(pg, "CREATE TABLE companies (ticker TEXT PRIMARY KEY, sector TEXT)")
    sql(pg, "CREATE TABLE corporate_actions (ticker TEXT, date DATE, action_type TEXT, value DOUBLE PRECISION)")
    sql(pg, "INSERT INTO companies VALUES ('ACME','Industrials'),('BETA','Technology'),('GAMA','Technology'),"
            "('DLTA','Energy'),('OLDD','Energy')")
    for ticker, close in (("BETA", 100), ("GAMA", 50), ("DLTA", 20), ("NOSEC", 10)):
        bar(pg, ticker, T, close, close)
    bar(pg, "OLDD", day(-3), 30, 30)
    signals = {}
    monkeypatch.setitem(paper.RULES, ("FIXTURE-TREND", "v1"), lambda conn, session: signals.get(session, []))

    def start(limits=LIMITS, strategy_id="FIXTURE-TREND", book_id="experimental"):
        inventory.load([fixture_row(strategy_id)])
        admission.create_book(book_id, starting_equity=100000, max_active_deployments=2, limits=limits, cost_model=COSTS)
        result = admission.admit(strategy_id, "v1", book_id=book_id, reviewer_run_id=signoff(strategy_id))
        assert result["outcome"] == "ADMITTED", result
        return result["deployment_id"]

    return SimpleDesk(pg, signals, start)


class SimpleDesk:
    def __init__(self, pg, signals, start):
        self.pg, self.signals, self.start = pg, signals, start

    def opportunities(self):
        return sql(self.pg, "SELECT ticker, status, reason_codes FROM office_opportunities ORDER BY recorded_at, signal_rank")

    def count(self, table):
        return sql(self.pg, f"SELECT count(*) FROM {table}")[0][0]


def test_no_signal_is_recorded_as_no_opportunity_and_creates_nothing(desk):
    deployment = desk.start()
    assert paper.scan(deployment, T) == {"session": str(T), "repeated": False, "counts": {"NO_OPPORTUNITY": 1}}
    assert paper.scan(deployment, T) == {"session": str(T), "repeated": True, "counts": {"NO_OPPORTUNITY": 1}}
    assert desk.opportunities() == [("", "NO_OPPORTUNITY", [])]
    assert desk.count("office_orders") == 0 and paper.fill_due("experimental") == {"filled": 0, "expired": 0, "waiting": 0}


def test_a_signal_becomes_one_sized_order_one_simulated_fill_one_exit_and_a_mark(desk):
    deployment = desk.start()
    desk.signals[T] = [Signal("ACME", "LONG", 2)]
    assert paper.scan(deployment, T)["counts"] == {"ACCEPTED": 1}
    assert paper.scan(deployment, T)["repeated"] and desk.count("office_orders") == 1          # a retry adds nothing
    order = sql(desk.pg, "SELECT ticker, side, shares, reference_price, reserved_usd, state, simulated FROM office_orders")[0]
    assert order == ("ACME", "LONG", Decimal("125"), Decimal("40"), Decimal("5000.00"), "PENDING", True)
    assert paper.fill_due("experimental") == {"filled": 0, "expired": 0, "waiting": 1}         # tomorrow's bar is not in yet
    bar(desk.pg, "ACME", day(1), 41, 42)
    assert paper.fill_due("experimental")["filled"] == 1 and paper.fill_due("experimental")["filled"] == 0
    fill = sql(desk.pg, "SELECT leg, fill_session, price, shares, price_source, cost_usd, simulated, broker_fill FROM office_fills")[0]
    assert fill == ("ENTRY", day(1), Decimal("41"), Decimal("125"), "BAR_OPEN", Decimal("5.1250"), True, False)
    assert paper.close_due("experimental") == {"closed": 0, "open": 1, "needs_review": []}     # held two sessions
    assert paper.mark("experimental", day(1))["equity_usd"] == "100119.88"                     # (42-41)*125 - 5.125
    bar(desk.pg, "ACME", day(2), 42, 43)
    assert paper.close_due("experimental")["closed"] == 1 and paper.close_due("experimental")["closed"] == 0
    position = sql(desk.pg, "SELECT state, entry_session, exit_session, exit_price, costs_usd, realized_pnl_usd FROM office_positions")[0]
    assert position == ("CLOSED", day(1), day(2), Decimal("43"), Decimal("10.5000"), Decimal("239.5000"))
    exit_fill = sql(desk.pg, "SELECT price_source, fill_session, broker_fill FROM office_fills WHERE leg = 'EXIT'")[0]
    assert exit_fill == ("BAR_CLOSE", day(2), False)
    marked = paper.mark("experimental", day(2))
    assert marked == {"session": str(day(2)), "equity_usd": "100239.50", "open_positions": 0, "created": True}
    assert not paper.mark("experimental", day(2))["created"] and desk.count("office_marks") == 2
    # every time is kept separately, and the fill is after the signal
    times = sql(desk.pg, "SELECT o.signal_session, o.recorded_at <= o.entry_due_at, r.decided_at <= o.entry_due_at, "
                         "f.fill_session FROM office_opportunities o JOIN office_orders r USING (opportunity_id) "
                         "JOIN office_fills f USING (order_id) WHERE f.leg = 'ENTRY'")[0]
    assert times == (T, True, True, day(1))


def test_a_late_worker_records_missed_and_never_a_backdated_order(desk):
    deployment = desk.start()
    desk.signals[day(-10)] = [Signal("ACME", "LONG", 2)]
    assert paper.scan(deployment, day(-10))["counts"] == {"MISSED": 1}
    bar(desk.pg, "BETA", day(1), 101, 102)                             # the entry bar is already known
    desk.signals[T] = [Signal("BETA", "LONG", 2)]
    assert paper.scan(deployment, T)["counts"] == {"MISSED": 1}
    assert desk.opportunities() == [("ACME", "MISSED", ["ENTRY_DEADLINE_PASSED"]),
                                    ("BETA", "MISSED", ["ENTRY_BAR_ALREADY_KNOWN"])]
    assert desk.count("office_orders") == 0 and desk.count("office_fills") == 0


def test_unsupported_stale_short_and_unknown_sector_signals_are_rejected_and_kept(desk):
    deployment = desk.start()
    desk.signals[T] = [Signal("NOPE", "LONG", 2), Signal("OLDD", "LONG", 2), Signal("BETA", "SHORT", 2),
                       Signal("NOSEC", "LONG", 2)]
    assert paper.scan(deployment, T)["counts"] == {"REJECTED": 4}
    assert desk.opportunities() == [("NOPE", "REJECTED", ["UNSUPPORTED_INSTRUMENT"]), ("OLDD", "REJECTED", ["STALE_PRICE"]),
                                    ("BETA", "REJECTED", ["SHORT_WITHOUT_BORROW_POLICY"]),
                                    ("NOSEC", "REJECTED", ["SECTOR_UNKNOWN"])]
    assert desk.count("office_orders") == 0


def test_capital_is_reserved_in_order_and_limits_hold_across_signals_and_scans(desk):
    deployment = desk.start({**LIMITS, "max_gross_pct": 12, "max_sector_pct": 8})
    desk.signals[T] = [Signal("BETA", "LONG", 3), Signal("GAMA", "LONG", 3), Signal("ACME", "LONG", 3),
                       Signal("DLTA", "LONG", 3)]
    assert paper.scan(deployment, T)["counts"] == {"ACCEPTED": 2, "REJECTED": 2}
    assert desk.opportunities() == [("BETA", "ACCEPTED", []), ("GAMA", "REJECTED", ["SECTOR_EXPOSURE_LIMIT"]),
                                    ("ACME", "ACCEPTED", []), ("DLTA", "REJECTED", ["GROSS_EXPOSURE_LIMIT"])]
    assert sql(desk.pg, "SELECT sum(reserved_usd) FROM office_orders")[0][0] == Decimal("10000.00")
    # the next session: the pending order for ACME blocks a second allocation to the same ticker
    bar(desk.pg, "ACME", day(1), 41, 42)
    desk.signals[day(1)] = [Signal("ACME", "LONG", 3)]
    assert paper.scan(deployment, day(1))["counts"] == {"REJECTED": 1}
    assert desk.opportunities()[-1] == ("ACME", "REJECTED", ["TICKER_ALREADY_HELD", "GROSS_EXPOSURE_LIMIT",
                                                         "SECTOR_EXPOSURE_LIMIT"])
    with pytest.raises(psycopg.errors.UniqueViolation):
        sql(desk.pg, "INSERT INTO office_orders SELECT %s, opportunity_id, deployment_id, book_id, ticker, side, shares, "
                     "reference_price, reserved_usd, signal_session, holding_sessions FROM office_orders WHERE ticker = 'ACME'",
            (str(uuid4()),))


def test_a_position_limit_and_a_drawdown_review_stop_new_entries(desk):
    deployment = desk.start({**LIMITS, "max_positions": 1})
    desk.signals[T] = [Signal("BETA", "LONG", 2), Signal("DLTA", "LONG", 2)]
    paper.scan(deployment, T)
    assert desk.opportunities() == [("BETA", "ACCEPTED", []), ("DLTA", "REJECTED", ["MAX_POSITIONS"])]
    sql(desk.pg, "INSERT INTO office_marks(book_id, session, equity_usd, realized_usd, unrealized_usd, gross_exposure_usd, "
                 "open_positions) VALUES ('experimental', %s, 89000, 0, -11000, 0, 0)", (day(-1),))
    bar(desk.pg, "DLTA", day(1), 20, 20)
    desk.signals[day(1)] = [Signal("DLTA", "LONG", 2)]
    paper.scan(deployment, day(1))
    assert desk.opportunities()[-1] == ("DLTA", "REJECTED", ["DRAWDOWN_REVIEW", "MAX_POSITIONS"])


def test_a_pause_stops_entries_and_lets_exits_and_marks_run(desk):
    deployment = desk.start()
    desk.signals[T] = [Signal("ACME", "LONG", 1)]
    paper.scan(deployment, T)
    bar(desk.pg, "ACME", day(1), 41, 42)
    bar(desk.pg, "BETA", day(1), 100, 100)
    assert paper.fill_due("experimental")["filled"] == 1
    sql(desk.pg, "UPDATE office_deployments SET state = 'PAUSED' WHERE deployment_id = %s", (deployment,))
    desk.signals[day(1)] = [Signal("BETA", "LONG", 1)]
    assert paper.scan(deployment, day(1))["counts"] == {"REJECTED": 1}
    assert desk.opportunities()[-1] == ("BETA", "REJECTED", ["DEPLOYMENT_PAUSED"]) and desk.count("office_orders") == 1
    assert paper.close_due("experimental")["closed"] == 1                 # held one session: out at that close
    assert paper.mark("experimental", day(1))["equity_usd"] == "100114.62"   # (42-41)*125 - 5.125 - 5.25


def test_a_short_needs_a_borrow_policy_and_pays_borrow(desk):
    deployment = desk.start({**LIMITS, "borrow_policy": "SIMULATED_FLAT_RATE"})
    desk.signals[T] = [Signal("BETA", "SHORT", 2)]
    assert paper.scan(deployment, T)["counts"] == {"ACCEPTED": 1}
    bar(desk.pg, "BETA", day(1), 100, 99)
    bar(desk.pg, "BETA", day(2), 98, 96)
    paper.fill_due("experimental")
    paper.close_due("experimental")
    # 50 shares short at 100, covered at 96: +200; fees 5.00 + 4.80; borrow 5000 * 3.65% * 1/365 = 0.50
    assert sql(desk.pg, "SELECT side, shares, costs_usd, realized_pnl_usd FROM office_positions")[0] == (
        "SHORT", Decimal("50"), Decimal("10.3000"), Decimal("189.7000"))
    assert sql(desk.pg, "SELECT costs->>'borrow_usd' FROM office_fills WHERE leg = 'EXIT'")[0][0] == "0.5000"


def test_a_split_during_the_holding_period_is_left_open_for_review(desk):
    deployment = desk.start()
    desk.signals[T] = [Signal("ACME", "LONG", 2)]
    paper.scan(deployment, T)
    bar(desk.pg, "ACME", day(1), 41, 42)
    bar(desk.pg, "ACME", day(2), 21, 21.5)
    sql(desk.pg, "INSERT INTO corporate_actions VALUES ('ACME', %s, 'split', 2)", (day(2),))
    paper.fill_due("experimental")
    assert paper.close_due("experimental") == {"closed": 0, "open": 0, "needs_review": [
        {"ticker": "ACME", "code": "SPLIT_DURING_HOLDING"}]}
    assert sql(desk.pg, "SELECT state FROM office_positions") == [("OPEN",)]


def test_an_order_whose_entry_price_never_arrives_expires_without_a_fill(desk):
    deployment = desk.start()
    desk.signals[T] = [Signal("ACME", "LONG", 2)]
    paper.scan(deployment, T)
    bar(desk.pg, "ACME", day(1), 0, 42)                                  # an unusable open
    assert paper.fill_due("experimental") == {"filled": 0, "expired": 1, "waiting": 0}
    assert sql(desk.pg, "SELECT state, reason_code FROM office_orders") == [("EXPIRED", "ENTRY_PRICE_INVALID")]
    assert desk.count("office_fills") == 0 and desk.count("office_positions") == 0


def test_the_database_refuses_broker_fills_backdated_fills_and_orders_without_an_accepted_opportunity(desk):
    deployment = desk.start()
    desk.signals[T] = [Signal("ACME", "LONG", 2), Signal("NOPE", "LONG", 2)]
    paper.scan(deployment, T)
    order = sql(desk.pg, "SELECT order_id FROM office_orders")[0][0]
    fill = ("INSERT INTO office_fills(fill_id, order_id, leg, fill_session, price, shares, price_source, cost_usd, costs, "
            "broker_fill, simulated) VALUES (%s, %s, 'ENTRY', %s, 41, 125, %s, 0, '{}'::jsonb, %s, %s)")
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="on or before its signal session"):
        sql(desk.pg, fill, (str(uuid4()), order, T, "BAR_OPEN", False, True))
    for source, broker, simulated in (("BAR_OPEN", True, True), ("BAR_OPEN", False, False), ("BROKER", False, True)):
        with pytest.raises(psycopg.errors.CheckViolation):
            sql(desk.pg, fill, (str(uuid4()), order, day(1), source, broker, simulated))
    rejected = sql(desk.pg, "SELECT opportunity_id FROM office_opportunities WHERE status = 'REJECTED'")[0][0]
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="needs an accepted opportunity"):
        sql(desk.pg, "INSERT INTO office_orders(order_id, opportunity_id, deployment_id, book_id, ticker, side, shares, "
                     "reference_price, reserved_usd, signal_session, holding_sessions) "
                     "VALUES (%s, %s, %s, 'experimental', 'NOPE', 'LONG', 1, 1, 1, %s, 2)",
            (str(uuid4()), rejected, deployment, T))
    bar(desk.pg, "ACME", day(1), 41, 42)
    bar(desk.pg, "ACME", day(2), 42, 43)
    paper.fill_due("experimental")
    paper.close_due("experimental")
    for statement, message in (("UPDATE office_fills SET price = 1", "written once"),
                               ("UPDATE office_opportunities SET status = 'ACCEPTED'", "written once"),
                               ("DELETE FROM office_orders", "written once"),
                               ("UPDATE office_orders SET state = 'PENDING'", "Only a pending office order"),
                               ("UPDATE office_positions SET realized_pnl_usd = 99999", "only be closed, once")):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match=message):
            sql(desk.pg, statement)


def test_nothing_runs_without_both_switches_or_without_a_registered_rule(desk, monkeypatch):
    deployment = desk.start()
    monkeypatch.delitem(paper.RULES, ("FIXTURE-TREND", "v1"))
    with pytest.raises(ValueError, match="DEPLOYMENT_NOT_SCANNABLE"):
        paper.scan(deployment, T)
    with pytest.raises(ValueError, match="DEPLOYMENT_NOT_FOUND"):
        paper.scan(str(uuid4()), T)
    monkeypatch.setenv("OFFICE_PAPER_ENABLED", "false")
    for call in (lambda: paper.scan(deployment, T), lambda: paper.fill_due("experimental"),
                 lambda: paper.close_due("experimental"), lambda: paper.mark("experimental", T)):
        with pytest.raises(config.OfficeDisabled, match="OFFICE_PAPER_DISABLED"):
            call()
    assert desk.count("office_opportunities") == 0
