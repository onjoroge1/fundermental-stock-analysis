"""The experimental paper book: scan a deployed strategy's frozen rule, then simulate what follows.

Everything here is a simulation on stored daily bars. Nothing is sent anywhere; there is no broker.

* ``scan`` runs the rule registered for a deployment's exact strategy version and records every signal it
  returns, taken or not. No signal is recorded as NO_OPPORTUNITY. A model never produces a signal.
* A signal from session D can only be entered at the next session's open. The order decision must be
  recorded before that open; a worker that arrives later records MISSED and no order.
* Sizes and limits are computed in code under one lock, so two scans cannot spend the same capital.
* ``fill_due`` fills a pending order at the next bar's open, ``close_due`` exits at the close of the last
  holding session, ``mark`` values the book. Exits and marks keep running while a deployment is paused.
* Costs are charged on every fill from the book's frozen cost model. Shorts need a borrow policy.

Not supported, and blocked rather than guessed: stops and targets inside a bar, options, and positions
that span a stock split (left open and reported for review).

``RULES`` is empty in this change. No real strategy has a rule registered, so nothing can be scanned.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from . import config, roles, tasks

ROLE = "scanner"
PAPER_LOCK = 0x0FF1CE04
NEW_YORK = ZoneInfo("America/New_York")
ORDER_EXPIRES_AFTER_DAYS = 7
REQUIRED_LIMITS = ("max_position_pct", "max_gross_pct", "max_positions")
MAX_SIGNALS_PER_SCAN = 50


@dataclass(frozen=True)
class Signal:
    ticker: str
    side: str                 # LONG or SHORT
    holding_sessions: int     # sessions held, counting the entry session; the exit is that session's close


# (strategy_id, version) -> rule(conn, session) -> signals in the strategy's own registered ranking order.
RULES: dict[tuple[str, str], Callable[[object, date], list[Signal]]] = {}


def entry_deadline(session: date) -> datetime:
    """09:30 New York on the next weekday after ``session``. A market holiday makes this early, never late."""
    day = session + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, time(9, 30), NEW_YORK)


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def _begin(conn) -> None:
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (PAPER_LOCK,))


def _book(conn, book_id: str) -> dict:
    row = conn.execute("SELECT starting_equity, limits, cost_model FROM office_books WHERE book_id = %s",
                       (book_id,)).fetchone()
    if row is None:
        raise ValueError("BOOK_NOT_FOUND")
    return {"starting_equity": Decimal(row[0]), "limits": row[1] or {}, "cost_model": row[2] or {}}


def _state(conn, book_id: str, book: dict) -> dict:
    """What the book holds right now, pending orders included: the basis for every limit check."""
    realized = Decimal(conn.execute(
        "SELECT COALESCE(sum(realized_pnl_usd), 0) FROM office_positions WHERE book_id = %s AND state = 'CLOSED'",
        (book_id,)).fetchone()[0])
    held = conn.execute(
        """SELECT p.ticker, p.shares * p.entry_price, c.sector FROM office_positions p
           LEFT JOIN companies c ON c.ticker = p.ticker WHERE p.book_id = %s AND p.state = 'OPEN'
           UNION ALL
           SELECT r.ticker, r.reserved_usd, c.sector FROM office_orders r
           LEFT JOIN companies c ON c.ticker = r.ticker WHERE r.book_id = %s AND r.state = 'PENDING'""",
        (book_id, book_id)).fetchall()
    peak, last = conn.execute(
        """SELECT max(equity_usd), (SELECT equity_usd FROM office_marks WHERE book_id = %s
                                    ORDER BY session DESC LIMIT 1) FROM office_marks WHERE book_id = %s""",
        (book_id, book_id)).fetchone()
    peak = max(Decimal(peak or 0), book["starting_equity"])
    return {"equity": book["starting_equity"] + realized, "held": [(t, Decimal(n), s) for t, n, s in held],
            "drawdown_pct": Decimal(0) if last is None else (peak - Decimal(last)) / peak * 100}


def _decide(conn, signal: Signal, session: date, *, paused: bool, late: bool, book: dict, state: dict):
    """Status, reason codes and sizing for one signal. Every failed check is listed, not just the first."""
    limits = book["limits"]
    if paused:
        return "REJECTED", ["DEPLOYMENT_PAUSED"], {}
    if late:
        return "MISSED", ["ENTRY_DEADLINE_PASSED"], {}
    if conn.execute("SELECT 1 FROM prices_daily WHERE ticker = %s AND date > %s LIMIT 1",
                    (signal.ticker, session)).fetchone():
        return "MISSED", ["ENTRY_BAR_ALREADY_KNOWN"], {}
    bar = conn.execute("SELECT date, close FROM prices_daily WHERE ticker = %s AND date <= %s "
                       "ORDER BY date DESC LIMIT 1", (signal.ticker, session)).fetchone()
    if bar is None:
        return "REJECTED", ["UNSUPPORTED_INSTRUMENT"], {}
    if bar[0] != session or bar[1] is None or not bar[1] > 0:
        return "REJECTED", ["STALE_PRICE"], {"last_bar": str(bar[0])}
    if any(not isinstance(limits.get(k), int | float) or limits[k] <= 0 for k in REQUIRED_LIMITS):
        return "REJECTED", ["BOOK_LIMITS_INCOMPLETE"], {}
    codes = []
    price, equity = Decimal(str(bar[1])), state["equity"]
    shares = math.floor(equity * Decimal(str(limits["max_position_pct"])) / 100 / price)
    notional = (shares * price).quantize(Decimal("0.01"))
    gross = sum(n for _, n, _ in state["held"])
    if signal.side == "SHORT" and not limits.get("borrow_policy"):
        codes.append("SHORT_WITHOUT_BORROW_POLICY")
    if any(t == signal.ticker for t, _, _ in state["held"]):
        codes.append("TICKER_ALREADY_HELD")
    if limits.get("drawdown_review_pct") and state["drawdown_pct"] >= Decimal(str(limits["drawdown_review_pct"])):
        codes.append("DRAWDOWN_REVIEW")
    if len(state["held"]) >= limits["max_positions"]:
        codes.append("MAX_POSITIONS")
    if shares < 1:
        codes.append("POSITION_TOO_SMALL")
    if gross + notional > equity * Decimal(str(limits["max_gross_pct"])) / 100:
        codes.append("GROSS_EXPOSURE_LIMIT")
    sector = None
    if limits.get("max_sector_pct"):
        found = conn.execute("SELECT sector FROM companies WHERE ticker = %s", (signal.ticker,)).fetchone()
        sector = found[0] if found else None
        if not sector:
            codes.append("SECTOR_UNKNOWN")
        elif (sum(n for _, n, s in state["held"] if s == sector) + notional
              > equity * Decimal(str(limits["max_sector_pct"])) / 100):
            codes.append("SECTOR_EXPOSURE_LIMIT")
    sizing = {"equity_usd": str(equity), "reference_close": str(price), "shares": shares,
              "notional_usd": str(notional), "gross_before_usd": str(gross)}
    if codes:
        return "REJECTED", codes, sizing
    state["held"].append((signal.ticker, notional, sector))          # reserved: later signals see it
    return "ACCEPTED", [], sizing


def scan(deployment_id: str, session: date) -> dict:
    """Run one deployment's rule for one session and record every signal. Repeating a scan changes nothing."""
    config.require_paper()
    for tool in ("read_prices", "read_paper_book", "read_strategy_evidence", "write_opportunity"):
        roles.authorize(ROLE, tool)
    with tasks.connect() as conn:
        _begin(conn)
        row = conn.execute(
            "SELECT strategy_id, version, book_id, state, execution_kind FROM office_deployments "
            "WHERE deployment_id = %s", (deployment_id,)).fetchone()
        if row is None:
            raise ValueError("DEPLOYMENT_NOT_FOUND")
        strategy_id, version, book_id, state_name, kind = row
        rule = RULES.get((strategy_id, version))
        if kind != "NATIVE" or state_name not in ("EXPERIMENTAL_PAPER", "PAUSED") or rule is None:
            raise ValueError("DEPLOYMENT_NOT_SCANNABLE")
        done = conn.execute(
            "SELECT status, count(*) FROM office_opportunities WHERE deployment_id = %s AND signal_session = %s "
            "GROUP BY status", (deployment_id, session)).fetchall()
        if done:
            return {"session": str(session), "repeated": True, "counts": dict(done)}
        signals = list(rule(conn, session))
        tickers = [s.ticker for s in signals]
        if (len(signals) > MAX_SIGNALS_PER_SCAN or len(set(tickers)) != len(tickers)
                or any(s.side not in ("LONG", "SHORT") or not 1 <= s.holding_sessions <= 260 for s in signals)):
            raise ValueError("RULE_OUTPUT_INVALID")
        if not signals:
            conn.execute(
                """INSERT INTO office_opportunities(opportunity_id, deployment_id, book_id, ticker, signal_session,
                       status) VALUES (%s, %s, %s, '', %s, 'NO_OPPORTUNITY')""",
                (str(uuid4()), deployment_id, book_id, session))
            return {"session": str(session), "repeated": False, "counts": {"NO_OPPORTUNITY": 1}}
        book, due = _book(conn, book_id), entry_deadline(session)
        book_state = _state(conn, book_id, book)
        late = conn.execute("SELECT now() > %s", (due,)).fetchone()[0]
        counts: dict[str, int] = {}
        for rank, signal in enumerate(signals, 1):
            status, codes, sizing = _decide(conn, signal, session, paused=state_name == "PAUSED", late=late,
                                            book=book, state=book_state)
            opportunity_id = str(uuid4())
            conn.execute(
                """INSERT INTO office_opportunities(opportunity_id, deployment_id, book_id, ticker, side,
                       signal_session, signal_rank, holding_sessions, entry_due_at, status, reason_codes, sizing)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (opportunity_id, deployment_id, book_id, signal.ticker, signal.side, session, rank,
                 signal.holding_sessions, due, status, Jsonb(codes), Jsonb(sizing)))
            if status == "ACCEPTED":
                conn.execute(
                    """INSERT INTO office_orders(order_id, opportunity_id, deployment_id, book_id, ticker, side,
                           shares, reference_price, reserved_usd, signal_session, holding_sessions)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (str(uuid4()), opportunity_id, deployment_id, book_id, signal.ticker, signal.side,
                     sizing["shares"], sizing["reference_close"], sizing["notional_usd"], session,
                     signal.holding_sessions))
            counts[status] = counts.get(status, 0) + 1
    return {"session": str(session), "repeated": False, "counts": counts}


def _fee(book: dict, shares, price) -> Decimal:
    return _money(Decimal(shares) * price * Decimal(str(book["cost_model"].get("bps_per_side", 0))) / 10_000)


def fill_due(book_id: str) -> dict:
    """Fill each pending order at the open of the first bar after its signal session, when that bar exists."""
    config.require_paper()
    out = {"filled": 0, "expired": 0, "waiting": 0}
    with tasks.connect() as conn:
        _begin(conn)
        book = _book(conn, book_id)
        orders = conn.execute(
            """SELECT order_id, deployment_id, ticker, side, shares, signal_session, holding_sessions
               FROM office_orders WHERE book_id = %s AND state = 'PENDING' ORDER BY decided_at, order_id""",
            (book_id,)).fetchall()
        for order_id, deployment_id, ticker, side, shares, session, holding in orders:
            bar = conn.execute("SELECT date, open FROM prices_daily WHERE ticker = %s AND date > %s "
                               "ORDER BY date LIMIT 1", (ticker, session)).fetchone()
            reason = None
            if bar is None:
                if date.today() - session <= timedelta(days=ORDER_EXPIRES_AFTER_DAYS):
                    out["waiting"] += 1
                    continue
                reason = "ENTRY_PRICE_UNAVAILABLE"
            elif bar[1] is None or not bar[1] > 0:
                reason = "ENTRY_PRICE_INVALID"
            if reason:
                conn.execute("UPDATE office_orders SET state = 'EXPIRED', reason_code = %s WHERE order_id = %s",
                             (reason, order_id))
                out["expired"] += 1
                continue
            price = Decimal(str(bar[1]))
            fee = _fee(book, shares, price)
            conn.execute(
                """INSERT INTO office_fills(fill_id, order_id, leg, fill_session, price, shares, price_source,
                       cost_usd, costs) VALUES (%s, %s, 'ENTRY', %s, %s, %s, 'BAR_OPEN', %s, %s)""",
                (str(uuid4()), order_id, bar[0], price, shares, fee,
                 Jsonb({"fee_usd": str(fee), "bps_per_side": book["cost_model"].get("bps_per_side", 0),
                        "model": "daily bar open, fixed basis points; not a broker fill"})))
            conn.execute(
                """INSERT INTO office_positions(position_id, order_id, deployment_id, book_id, ticker, side, shares,
                       entry_session, entry_price, holding_sessions, costs_usd)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (str(uuid4()), order_id, deployment_id, book_id, ticker, side, shares, bar[0], price, holding, fee))
            conn.execute("UPDATE office_orders SET state = 'FILLED' WHERE order_id = %s", (order_id,))
            out["filled"] += 1
    return out


def close_due(book_id: str) -> dict:
    """Exit each open position at the close of its last holding session, once that bar exists."""
    config.require_paper()
    out = {"closed": 0, "open": 0, "needs_review": []}
    with tasks.connect() as conn:
        _begin(conn)
        book = _book(conn, book_id)
        positions = conn.execute(
            """SELECT position_id, order_id, ticker, side, shares, entry_session, entry_price, holding_sessions,
                      costs_usd FROM office_positions WHERE book_id = %s AND state = 'OPEN'
               ORDER BY entry_session, position_id""", (book_id,)).fetchall()
        for position_id, order_id, ticker, side, shares, entered, entry_price, holding, costs in positions:
            bar = conn.execute("SELECT date, close FROM prices_daily WHERE ticker = %s AND date >= %s "
                               "ORDER BY date OFFSET %s LIMIT 1", (ticker, entered, holding - 1)).fetchone()
            if bar is None or bar[1] is None or not bar[1] > 0:
                out["open"] += 1
                continue
            if conn.execute("SELECT 1 FROM corporate_actions WHERE ticker = %s AND date > %s AND date <= %s "
                            "AND lower(action_type) LIKE '%%split%%' LIMIT 1", (ticker, entered, bar[0])).fetchone():
                out["needs_review"].append({"ticker": ticker, "code": "SPLIT_DURING_HOLDING"})
                continue
            price = Decimal(str(bar[1]))
            fee = _fee(book, shares, price)
            borrow = Decimal(0)
            if side == "SHORT":
                rate = Decimal(str(book["cost_model"].get("borrow_bps_per_year", 0)))
                borrow = _money(shares * entry_price * rate / 10_000 * (bar[0] - entered).days / 365)
            gross = (price - entry_price) * shares * (1 if side == "LONG" else -1)
            total_costs = costs + fee + borrow
            conn.execute(
                """INSERT INTO office_fills(fill_id, order_id, leg, fill_session, price, shares, price_source,
                       cost_usd, costs) VALUES (%s, %s, 'EXIT', %s, %s, %s, 'BAR_CLOSE', %s, %s)""",
                (str(uuid4()), order_id, bar[0], price, shares, fee + borrow,
                 Jsonb({"fee_usd": str(fee), "borrow_usd": str(borrow),
                        "model": "daily bar close, fixed basis points; not a broker fill"})))
            conn.execute(
                """UPDATE office_positions SET state = 'CLOSED', exit_session = %s, exit_price = %s, costs_usd = %s,
                       realized_pnl_usd = %s WHERE position_id = %s""",
                (bar[0], price, total_costs, _money(gross - total_costs), position_id))
            out["closed"] += 1
    return out


def mark(book_id: str, session: date) -> dict:
    """Value the book at one session's closes. One mark per book and session; asking again returns it."""
    config.require_paper()
    with tasks.connect() as conn:
        _begin(conn)
        book = _book(conn, book_id)
        realized = Decimal(conn.execute(
            "SELECT COALESCE(sum(realized_pnl_usd), 0) FROM office_positions WHERE book_id = %s "
            "AND state = 'CLOSED' AND exit_session <= %s", (book_id, session)).fetchone()[0])
        held = conn.execute(
            """SELECT ticker, side, shares, entry_price, costs_usd FROM office_positions
               WHERE book_id = %s AND entry_session <= %s AND (state = 'OPEN' OR exit_session > %s)
               ORDER BY ticker""", (book_id, session, session)).fetchall()
        unrealized, gross, stale = Decimal(0), Decimal(0), []
        for ticker, side, shares, entry_price, costs in held:
            bar = conn.execute("SELECT date, close FROM prices_daily WHERE ticker = %s AND date <= %s "
                               "AND close > 0 ORDER BY date DESC LIMIT 1", (ticker, session)).fetchone()
            price = entry_price if bar is None else Decimal(str(bar[1]))
            if bar is None or bar[0] != session:
                stale.append(ticker)
            unrealized += (price - entry_price) * shares * (1 if side == "LONG" else -1) - costs
            gross += shares * price
        equity = book["starting_equity"] + realized + unrealized
        row = conn.execute(
            """INSERT INTO office_marks(book_id, session, equity_usd, realized_usd, unrealized_usd,
                   gross_exposure_usd, open_positions, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (book_id, session) DO NOTHING RETURNING equity_usd""",
            (book_id, session, equity, realized, unrealized, gross, len(held),
             Jsonb({"stale_prices": stale, "basis": "daily bar closes; simulated"}))).fetchone()
        created = row is not None
        if not created:
            equity, len_held = conn.execute(
                "SELECT equity_usd, open_positions FROM office_marks WHERE book_id = %s AND session = %s",
                (book_id, session)).fetchone()
            return {"session": str(session), "equity_usd": str(equity), "open_positions": len_held, "created": False}
    return {"session": str(session), "equity_usd": str(equity.quantize(Decimal("0.01"))),
            "open_positions": len(held), "created": True}
