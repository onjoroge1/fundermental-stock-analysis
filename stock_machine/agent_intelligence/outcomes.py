"""Execution-backed learning, isolated from the legacy hypothetical v2 rewards.

New stock arms learn only from decision-linked realized paper positions. Valid
flat decisions have a separately labelled 20-session abstention observation.
Blocked inputs, rejected orders, holds and legacy retrospective simulations do
not become successful zero-return training samples.
"""

from __future__ import annotations

from math import isfinite
from .learning import record_outcome
from .. import db
from ..market_calendar import latest_completed_session, session_offset, session_dates

HORIZON_SESSIONS = 20
ROUND_TRIP_COST_PCT = 0.20  # legacy helper default; realized learning uses saved fills
CAPITAL_USED_PCT = 10.0
TURNOVER_PCT = 20.0


def _drawdown(values: list[float]) -> float:
    if not values:
        raise ValueError("OUTCOME_PATH_EMPTY")
    peak, worst = values[0], 0.0
    for value in values:
        if peak > 0:
            worst = min(worst, (value / peak - 1) * 100)
        peak = max(peak, value)
    return worst


def _stock_outcome(conn, ticker: str, action: str, entry: str, exit_day: str) -> dict:
    if action == "NO_TRADE":
        return {
            "status": "MATURED",
            "gross_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "capital_used_pct": 0.0,
            "turnover_pct": 0.0,
            "costs_pct": 0.0,
            "entry_date": entry,
            "exit_date": exit_day,
        }
    rows = db.fetch_prices(conn, ticker, exit_day)
    by_date = {r["date"]: r for r in rows if entry <= r["date"] <= exit_day}
    required = session_dates(entry, exit_day)
    if any(day not in by_date for day in required):
        raise ValueError("OUTCOME_PATH_SESSION_MISSING")
    prices = [by_date[day].get("adj_close") for day in required]
    if not prices or any(
        isinstance(p, bool)
        or not isinstance(p, (int, float))
        or not isfinite(p)
        or p <= 0
        for p in prices
    ):
        raise ValueError("OUTCOME_ADJUSTED_PRICE_INVALID")
    p0, p1 = prices[0], prices[-1]
    sign = 1 if action == "LONG_STOCK" else -1
    values = [1 + sign * (p / p0 - 1) for p in prices]
    return {
        "status": "MATURED",
        "gross_return_pct": round(sign * (p1 / p0 - 1) * 100, 6),
        "max_drawdown_pct": round(_drawdown(values), 6),
        "capital_used_pct": CAPITAL_USED_PCT,
        "turnover_pct": TURNOVER_PCT,
        "costs_pct": ROUND_TRIP_COST_PCT,
        "entry_date": entry,
        "exit_date": exit_day,
        "entry_adjusted_close": p0,
        "exit_adjusted_close": p1,
        "observations": len(values),
        "return_basis": "same_vintage_adjusted_endpoints.v1",
    }


def execution(conn, decision_id: str) -> dict | None:
    """Read the actual ledger; optional storage is absent in research-only mode."""
    if conn.execute("SELECT to_regclass('agent_trade_intents')").fetchone()[0] is None:
        return None
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT i.status AS intent_status,i.action AS intent_action,
            i.risk_snapshot,p.position_id::text,p.side,p.status AS position_status,
            p.entry_market_date::text,p.exit_market_date::text,p.entry_notional_usd,
            p.entry_cost_usd,p.exit_cost_usd,p.realized_pnl_usd
            FROM agent_trade_intents i LEFT JOIN agent_paper_positions p
                ON p.source_intent_id=i.intent_id
            WHERE i.decision_id=%s""",
            (decision_id,),
        )
        return cur.fetchone()


def learning_status(
    run: dict, ledger: dict | None, completed: str
) -> tuple[str, str | None]:
    """Shared status for scanner and owner UI; no guessed horizon from state date."""
    action = ((run.get("bandit") or {}).get("selected") or {}).get("action")
    if run.get("learning_contract") != "executed-paper.v3":
        return "EXCLUDED_LEGACY_HYPOTHETICAL", None
    if run.get("mode") != "PAPER":
        return "EXCLUDED_SHADOW", None
    if not (run.get("state") or {}).get("paper_eligible"):
        return "EXCLUDED_BLOCKED_STATE", None
    if not ledger or ledger["intent_status"] == "PENDING":
        return "PENDING_EXECUTION", None
    if ledger["intent_status"] == "BLOCKED":
        return "EXCLUDED_REJECTED_INTENT", None
    risk = ledger.get("risk_snapshot") or {}
    if risk.get("execution_contract") != "prospective-next-close.v2":
        return "EXCLUDED_LEGACY_FILL", None
    if (
        action == "NO_TRADE"
        and ledger["intent_action"] == "NO_TRADE"
        and ledger["intent_status"] == "NO_ACTION"
    ):
        day = risk.get("execution_session")
        if not day:
            return "EXCLUDED_INVALID_ABSTENTION", None
        due = session_offset(day, HORIZON_SESSIONS)
        return ("READY_ABSTENTION" if completed >= due else "PENDING_MATURITY"), due
    wanted = {"LONG_STOCK": "OPEN_LONG", "SHORT_STOCK": "OPEN_SHORT"}.get(action)
    if not wanted or ledger["intent_action"] != wanted or not ledger.get("position_id"):
        return "EXCLUDED_NO_NEW_POSITION", None
    if ledger["intent_status"] != "SIMULATED":
        return "EXCLUDED_UNFILLED_INTENT", None
    due = session_offset(ledger["entry_market_date"], HORIZON_SESSIONS)
    if ledger["position_status"] == "CLOSED":
        return "READY_REALIZED", ledger["exit_market_date"]
    return "PENDING_EXIT" if completed >= due else "PENDING_MATURITY", due


def score_matured(*, limit: int = 100) -> dict:
    from .. import research_store

    if not 1 <= limit <= 1000:
        raise ValueError("OUTCOME_LIMIT_INVALID")
    completed = latest_completed_session()
    with db.connect() as conn:
        records = research_store.outcome_candidates(conn, limit=limit)
    results = []
    for record in records:
        run = record.get("payload") or {}
        ticker, key = run.get("ticker"), run.get("decision_id")
        if not ticker or not key:
            continue
        action = ((run.get("bandit") or {}).get("selected") or {}).get("action")
        if str(action).startswith("OPTION:"):
            from .option_paper import settle_if_matured

            try:
                result = settle_if_matured(ticker, key)
                results.append(
                    {
                        "ticker": ticker,
                        "decision_id": key,
                        "status": "OPTION_" + result.get("status", "UNKNOWN"),
                        "option_outcome": result,
                    }
                )
            except ValueError as exc:
                results.append(
                    {
                        "ticker": ticker,
                        "decision_id": key,
                        "status": "BLOCKED_INPUTS",
                        "reason": str(exc),
                    }
                )
            continue
        try:
            with db.connect() as conn:
                if research_store.get(conn, "AGENT_REWARD_V3", key):
                    continue
                # Legacy records are retained but do not train the new model.
                ledger = (
                    execution(conn, key)
                    if run.get("learning_contract") == "executed-paper.v3"
                    else None
                )
                status, due = learning_status(run, ledger, completed)
                row = {
                    "ticker": ticker,
                    "decision_id": key,
                    "status": status,
                    "due_session": due,
                }
                if status.startswith("EXCLUDED_"):
                    research_store.save(
                        conn, "AGENT_OUTCOME_EXCLUSION_V1", key, row, ticker
                    )
                    results.append(row)
                    continue
                if status not in {"READY_ABSTENTION", "READY_REALIZED"}:
                    results.append(row)
                    continue
                if status == "READY_ABSTENTION":
                    entry = ledger["risk_snapshot"]["execution_session"]
                    outcome = _stock_outcome(conn, ticker, "NO_TRADE", entry, due)
                    outcome["learning_basis"] = "PAPER_ABSTENTION_V1"
                else:
                    outcome = _stock_outcome(
                        conn,
                        ticker,
                        action,
                        ledger["entry_market_date"],
                        ledger["exit_market_date"],
                    )
                    notional = float(ledger["entry_notional_usd"])
                    costs = float(ledger["entry_cost_usd"]) + float(
                        ledger["exit_cost_usd"]
                    )
                    equity = float(ledger["risk_snapshot"]["equity_before_usd"])
                    # Saved realized cash P&L is authoritative; current price
                    # history supplies only the path drawdown, never a new fill.
                    outcome.update(
                        gross_return_pct=(float(ledger["realized_pnl_usd"]) + costs)
                        / notional
                        * 100,
                        costs_pct=costs / notional * 100,
                        capital_used_pct=notional / equity * 100,
                        turnover_pct=2 * notional / equity * 100,
                        position_id=ledger["position_id"],
                        learning_basis="REALIZED_PAPER_FILL_V1",
                        realized_pnl_usd=ledger["realized_pnl_usd"],
                    )
            learned = record_outcome(ticker, key, outcome)
            results.append(
                {
                    **row,
                    "status": "SCORED",
                    "action": action,
                    "outcome": outcome,
                    "reward": learned["reward_record"]["reward"],
                }
            )
        except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
            results.append(
                {
                    "status": "BLOCKED_INPUTS",
                    "ticker": ticker,
                    "decision_id": key,
                    "reason": (
                        str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    ),
                }
            )
    blocked = sum(r["status"] in {"BLOCKED_INPUTS", "PENDING_EXIT"} for r in results)
    return {
        "schema_version": "agent-intelligence-outcomes.v2",
        "status": "ATTENTION" if blocked else "OK",
        "latest_completed_session": completed,
        "horizon_sessions": HORIZON_SESSIONS,
        "results": results,
        "scored": sum(r["status"] == "SCORED" for r in results),
        "pending": sum(r["status"].startswith("PENDING_") for r in results),
        "excluded": sum(r["status"].startswith("EXCLUDED_") for r in results),
        "blocked": blocked,
        "broker_submission": False,
    }
