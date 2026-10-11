"""Prospective counterfactual learning for the pooled stock bandit.

A stock's forward return is observable whether or not the agent traded, so
every eligible decision yields a fixed-window outcome for both stock
directions: entry at the first exchange close after the decision was
recorded (the same session a paper fill would use), exit 20 sessions later,
fixed paper costs. Nothing starts before the decision is recorded and no
outcome is rewritten. Daily decisions on one stock share most of a window,
so each carries 1/20 of an independent observation's weight. Realized paper
P&L stays in the paper ledger and weekly audit as execution evidence.
Blocked inputs and legacy contracts never become training samples.
"""

from __future__ import annotations

from math import isfinite
from .learning import record_counterfactual
from .. import db
from ..regime import sector_etf
from ..market_calendar import (
    latest_completed_session,
    next_close_after,
    session_offset,
    session_dates,
)

CONTRACT = "prospective-counterfactual.v1"
HORIZON_SESSIONS = 20
LEARNED_ARMS = ("LONG_STOCK", "SHORT_STOCK")
OVERLAP_WEIGHT = 1.0 / HORIZON_SESSIONS
FILL_COST_BPS_PER_SIDE = 10.0
CHECK_KIND = "AGENT_OUTCOME_CHECK_V1"


def learning_window(conn, decision_id: str, observed_iso: str) -> dict:
    """Frozen at decision time: the session a paper fill would use and its target.

    Uses the journal recording time, exactly as the paper ledger does, so a
    counterfactual and an executed trade share one entry close.
    """
    from uuid import UUID

    try:
        UUID(str(decision_id))
    except ValueError:
        # Journal ids are UUIDs; a malformed id must not abort the transaction.
        row = None
    else:
        row = conn.execute(
            "SELECT recorded_at::text FROM agent_lab_decisions WHERE decision_id=%s",
            (str(decision_id),),
        ).fetchone()
    recorded = row[0] if row and isinstance(row[0], str) else None
    entry = next_close_after(recorded or observed_iso)
    return {
        "contract": CONTRACT,
        "execution_session": entry,
        "due_session": session_offset(entry, HORIZON_SESSIONS),
        "horizon_sessions": HORIZON_SESSIONS,
        "learned_arms": list(LEARNED_ARMS),
        "overlap_weight": OVERLAP_WEIGHT,
        "entry_basis": "journal_recorded_at" if recorded else "observed_at",
    }


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
    if action not in LEARNED_ARMS:
        raise ValueError("OUTCOME_ACTION_NOT_LEARNED")
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
        "entry_date": entry,
        "exit_date": exit_day,
        "entry_adjusted_close": p0,
        "exit_adjusted_close": p1,
        "observations": len(values),
        "return_basis": "same_vintage_adjusted_endpoints.v1",
    }


def learning_status(run: dict, completed: str) -> tuple[str, str | None]:
    """Shared status for scanner and owner UI; the window was frozen at decision time."""
    if run.get("learning_contract") != CONTRACT:
        if run.get("learning_contract") == "executed-paper.v3":
            return "EXCLUDED_PRIOR_LEARNING_CONTRACT", None
        return "EXCLUDED_LEGACY_HYPOTHETICAL", None
    if not (run.get("state") or {}).get("paper_eligible"):
        return "EXCLUDED_BLOCKED_STATE", None
    window = run.get("learning") or {}
    due, entry = window.get("due_session"), window.get("execution_session")
    if window.get("contract") != CONTRACT or not due or not entry or entry > due:
        return "EXCLUDED_INVALID_LEARNING_WINDOW", None
    return ("READY_COUNTERFACTUAL" if completed >= due else "PENDING_MATURITY"), due


BETA_RANGE = (0.0, 3.0)
DEFAULT_BETA = 1.0


def frozen_beta(run: dict) -> tuple[float, str]:
    """Beta to SPY known at decision time (63-session), clipped; 1.0 when missing."""
    value = (((run.get("state") or {}).get("technical") or {}).get("features") or {}).get(
        "beta_63_vs_spy"
    )
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return DEFAULT_BETA, "default_beta"
    return min(BETA_RANGE[1], max(BETA_RANGE[0], float(value))), "frozen_beta_63_vs_spy"


def counterfactual_outcomes(conn, ticker: str, entry: str, due: str, beta=None, sector_symbol=None) -> dict:
    """Both directions over the frozen window.

    gross_return_pct stays the raw stock return (the paper ledger reconciles
    against it); residual_return_pct removes beta times SPY's return over the
    same window and is what the model learns from. Same-day labels across
    stocks then no longer share the market's move.
    """
    cost_pct = 2 * FILL_COST_BPS_PER_SIDE / 100
    from ..agent_trading import TARGET_POSITION_PCT

    beta, basis = beta if beta is not None else (DEFAULT_BETA, "default_beta")
    market_pct = _stock_outcome(conn, "SPY", "LONG_STOCK", entry, due)["gross_return_pct"]
    # Diagnostic only (never the learning label): return relative to the
    # sector ETF over the same window; missing sector data does not block.
    sector_pct = None
    if sector_symbol:
        try:
            sector_pct = _stock_outcome(conn, sector_symbol, "LONG_STOCK", entry, due)["gross_return_pct"]
        except ValueError:
            sector_pct = None
    size_pct = TARGET_POSITION_PCT * 100
    result = {}
    for action in LEARNED_ARMS:
        outcome = _stock_outcome(conn, ticker, action, entry, due)
        sign = 1 if action == "LONG_STOCK" else -1
        outcome.update(
            costs_pct=cost_pct,
            capital_used_pct=size_pct,
            turnover_pct=2 * size_pct,
            learning_basis="PROSPECTIVE_COUNTERFACTUAL_V1",
            market_return_pct=market_pct,
            beta=beta,
            beta_basis=basis,
            residual_return_pct=round(outcome["gross_return_pct"] - sign * beta * market_pct, 6),
            sector_symbol=sector_symbol,
            sector_relative_return_pct=(
                None if sector_pct is None else round(outcome["gross_return_pct"] - sign * sector_pct, 6)
            ),
        )
        result[action] = outcome
    return result


def record_check(row: dict, completed: str, *, conn=None) -> str:
    """Durable once-per-session attempt record for a blocked outcome.

    The candidate query skips decisions already checked this session, so a
    pile of incomplete price paths cannot fill every bounded pass; each one
    retries after the next completed session. If the schema predates the
    check kind, scanning falls back to retrying every pass. A scanner may pass
    its autocommit connection; the write is then one transaction on it.
    """
    from .. import research_store

    key = f"{row['decision_id']}:{completed}"

    def write(c):
        # One record per decision and session; a later attempt in the same
        # session (with a different reason) is already covered.
        if research_store.get(c, CHECK_KIND, key):
            return
        with c.transaction():
            research_store.save(
                c, CHECK_KIND, key, {**row, "session": completed}, row.get("ticker")
            )

    try:
        if conn is None:
            with db.connect() as own:
                write(own)
        else:
            with conn.transaction():
                write(conn)
        return "NEXT_SESSION"
    except Exception:
        return "EVERY_PASS_CHECK_UNAVAILABLE"


def _score_one(conn, run: dict, completed: str) -> dict | None:
    from .. import research_store

    ticker, key = run.get("ticker"), run.get("decision_id")
    if not ticker or not key:
        return None
    action = ((run.get("bandit") or {}).get("selected") or {}).get("action")
    if str(action).startswith("OPTION:"):
        from .option_paper import settle_if_matured

        try:
            result = settle_if_matured(ticker, key)
            return {
                "ticker": ticker,
                "decision_id": key,
                "status": "OPTION_" + result.get("status", "UNKNOWN"),
                "option_outcome": result,
            }
        except ValueError as exc:
            return {
                "ticker": ticker,
                "decision_id": key,
                "status": "BLOCKED_INPUTS",
                "reason": str(exc),
            }
    try:
        with conn.transaction():
            if research_store.get(conn, "AGENT_REWARD_V3", key):
                return None
            status, due = learning_status(run, completed)
            row = {
                "ticker": ticker,
                "decision_id": key,
                "status": status,
                "due_session": due,
            }
            if status.startswith("EXCLUDED_"):
                research_store.save(conn, "AGENT_OUTCOME_EXCLUSION_V1", key, row, ticker)
                return row
            if status != "READY_COUNTERFACTUAL":
                return row
            arm_outcomes = counterfactual_outcomes(
                conn, ticker, run["learning"]["execution_session"], due, frozen_beta(run),
                sector_etf((run.get("state") or {}).get("sector")),
            )
            # Nested: the reward record joins this decision's transaction.
            learned = record_counterfactual(ticker, key, arm_outcomes, conn=conn)
        return {
            **row,
            "status": "SCORED",
            "selected_action": action,
            "rewards": {
                a: v["reward"]["reward"]
                for a, v in learned["reward_record"]["arms"].items()
            },
        }
    except (ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
        row = {
            "status": "BLOCKED_INPUTS",
            "ticker": ticker,
            "decision_id": key,
            "reason": (
                str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            ),
        }
        row["retry"] = record_check(row, completed, conn=conn)
        return row


def score_matured(*, limit: int = 100) -> dict:
    from .. import research_store

    if not 1 <= limit <= 1000:
        raise ValueError("OUTCOME_LIMIT_INVALID")
    completed = latest_completed_session()
    results = []
    # One connection per pass, one transaction per decision: the scan once
    # opened two or three connections for every record it scored.
    with db.connect() as conn:
        conn.autocommit = True
        records = research_store.outcome_candidates(
            conn, limit=limit, completed=completed, contract=CONTRACT
        )
        for record in records:
            row = _score_one(conn, record.get("payload") or {}, completed)
            if row is not None:
                results.append(row)
    blocked = sum(r["status"] == "BLOCKED_INPUTS" for r in results)
    return {
        "schema_version": "agent-intelligence-outcomes.v3",
        "learning_contract": CONTRACT,
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
