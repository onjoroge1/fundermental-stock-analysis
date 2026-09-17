"""Agent Trading v1: decision-linked deterministic paper execution.

The research agent never talks to a broker. A recorded research decision may be
translated into an equity-only paper intent by fixed rules and fixed portfolio
risk limits. All fills are simulated from the latest completed-session adjusted
close and are explicitly marked simulated.
"""
from __future__ import annotations

from uuid import uuid4

from psycopg.types.json import Jsonb

from . import db
from .agents.contracts import PILOT
from .market_calendar import latest_completed_session

STARTING_EQUITY_USD = 100_000.0
MAX_POSITION_PCT = 0.10
MAX_GROSS_PCT = 0.50
MAX_OPEN_POSITIONS = 5
MIN_TRADE_NOTIONAL_USD = 500.0
FILL_COST_BPS = 10.0


def desired_side(classification: str | None) -> str:
    return {"ATTRACTIVE": "LONG", "UNATTRACTIVE": "SHORT"}.get(
        str(classification or "").upper(), "FLAT"
    )


def deterministic_action(desired: str, current_side: str | None) -> str:
    if current_side is None:
        return {"LONG": "OPEN_LONG", "SHORT": "OPEN_SHORT"}.get(desired, "NO_TRADE")
    if desired == current_side:
        return "HOLD"
    # Flatten first. A reversal requires a later independently recorded decision.
    return "CLOSE"


def position_budget(equity: float, gross: float, open_count: int) -> tuple[float, list[str]]:
    blockers: list[str] = []
    if equity <= 0:
        blockers.append("PAPER_EQUITY_NOT_POSITIVE")
    if open_count >= MAX_OPEN_POSITIONS:
        blockers.append("MAX_OPEN_POSITIONS_REACHED")
    capacity = max(0.0, equity * MAX_GROSS_PCT - gross)
    target = max(0.0, min(equity * MAX_POSITION_PCT, capacity))
    if target < MIN_TRADE_NOTIONAL_USD:
        blockers.append("INSUFFICIENT_GROSS_CAPACITY")
    return round(target, 2), blockers


def _latest_price(conn, ticker: str) -> tuple[str | None, float | None]:
    expected = latest_completed_session()
    row = conn.execute(
        """SELECT date::text,COALESCE(adj_close,close) FROM prices_daily
           WHERE ticker=%s AND date<=%s ORDER BY date DESC LIMIT 1""",
        (ticker, expected),
    ).fetchone()
    if not row or row[0] != expected or not row[1] or float(row[1]) <= 0:
        return (row[0] if row else None), None
    return row[0], float(row[1])


def _open_position(conn, ticker: str) -> dict | None:
    row = conn.execute(
        """SELECT position_id::text,ticker,side,source_decision_id::text,
                  source_intent_id::text,entry_market_date::text,entry_price,
                  paper_units,entry_notional_usd,entry_cost_usd
           FROM agent_paper_positions WHERE ticker=%s AND status='OPEN'""",
        (ticker,),
    ).fetchone()
    if not row:
        return None
    cols = ("position_id","ticker","side","source_decision_id","source_intent_id",
            "entry_market_date","entry_price","paper_units","entry_notional_usd","entry_cost_usd")
    return dict(zip(cols, row))


def _report_classification(conn, decision: dict) -> tuple[str | None, list[str]]:
    report_id = decision.get("source_report_id")
    if not report_id:
        return None, ["EXACT_SOURCE_REPORT_MISSING"]
    row = conn.execute("SELECT report FROM analysis_reports WHERE report_id=%s", (report_id,)).fetchone()
    if not row:
        return None, ["EXACT_SOURCE_REPORT_NOT_FOUND"]
    report = row[0] or {}
    if report.get("ticker", "").upper() != decision.get("ticker"):
        return None, ["SOURCE_REPORT_TICKER_MISMATCH"]
    classification = ((report.get("conclusion") or {}).get("classification") or "").upper()
    if classification not in {"ATTRACTIVE", "UNATTRACTIVE", "WATCH", "INSUFFICIENT_DATA"}:
        return None, ["SOURCE_CLASSIFICATION_NOT_SUPPORTED"]
    return classification, []


def _portfolio_state(conn, *, require_complete_prices: bool = False) -> dict:
    rows = conn.execute(
        """SELECT position_id::text,ticker,side,entry_price,paper_units,entry_cost_usd
           FROM agent_paper_positions WHERE status='OPEN' ORDER BY ticker"""
    ).fetchall()
    realized = float(conn.execute(
        "SELECT COALESCE(sum(realized_pnl_usd),0) FROM agent_paper_positions WHERE status='CLOSED'"
    ).fetchone()[0] or 0)
    open_rows, unrealized, gross, missing = [], 0.0, 0.0, []
    for pid, ticker, side, entry, units, entry_cost in rows:
        market_date, price = _latest_price(conn, ticker)
        if price is None:
            missing.append(ticker)
            open_rows.append({"position_id": pid, "ticker": ticker, "side": side,
                              "entry_price": entry, "paper_units": units,
                              "market_date": market_date, "price": None,
                              "unrealized_pnl_usd": None})
            continue
        sign = 1.0 if side == "LONG" else -1.0
        pnl = sign * (float(price) - float(entry)) * float(units) - float(entry_cost or 0)
        notional = abs(float(units) * float(price))
        unrealized += pnl
        gross += notional
        open_rows.append({"position_id": pid, "ticker": ticker, "side": side,
                          "entry_price": float(entry), "paper_units": float(units),
                          "market_date": market_date, "price": float(price),
                          "gross_notional_usd": round(notional, 2),
                          "unrealized_pnl_usd": round(pnl, 2)})
    if require_complete_prices and missing:
        raise ValueError("PAPER_MARK_PRICE_MISSING")
    equity = STARTING_EQUITY_USD + realized + unrealized
    return {"starting_equity_usd": STARTING_EQUITY_USD,
            "equity_usd": round(equity, 2), "realized_pnl_usd": round(realized, 2),
            "unrealized_pnl_usd": round(unrealized, 2), "gross_exposure_usd": round(gross, 2),
            "gross_exposure_pct": round((gross / equity * 100), 2) if equity > 0 else None,
            "open_count": len(rows), "positions": open_rows, "missing_prices": missing,
            "limits": {"max_position_pct": MAX_POSITION_PCT * 100,
                       "max_gross_pct": MAX_GROSS_PCT * 100,
                       "max_open_positions": MAX_OPEN_POSITIONS,
                       "fill_cost_bps": FILL_COST_BPS}}


def portfolio() -> dict:
    with db.connect() as conn:
        return _portfolio_state(conn)


def mark_open_positions() -> dict:
    with db.connect() as conn:
        state = _portfolio_state(conn, require_complete_prices=True)
        for p in state["positions"]:
            conn.execute(
                """INSERT INTO agent_paper_marks(position_id,market_date,price,unrealized_pnl_usd,gross_notional_usd)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT (position_id,market_date) DO NOTHING""",
                (p["position_id"], p["market_date"], p["price"], p["unrealized_pnl_usd"], p["gross_notional_usd"]),
            )
        return state


def _existing_intent(conn, decision_id: str) -> dict | None:
    row = conn.execute(
        """SELECT intent_id::text,ticker,classification,desired_side,action,status,
                  market_date::text,reference_price,target_notional_usd,rationale,blockers,risk_snapshot
           FROM agent_trade_intents WHERE decision_id=%s""", (decision_id,)
    ).fetchone()
    if not row:
        return None
    cols = ("intent_id","ticker","classification","desired_side","action","status","market_date",
            "reference_price","target_notional_usd","rationale","blockers","risk_snapshot")
    return {**dict(zip(cols, row)), "replayed": True, "execution_mode": "PAPER", "broker_submission": False}


def process_decision(decision: dict) -> dict:
    """Create at most one paper intent/fill sequence for one frozen agent decision."""
    ticker = str(decision.get("ticker") or "").upper()
    decision_id = str(decision.get("decision_id") or "")
    if ticker not in PILOT or not decision_id:
        raise ValueError("INVALID_AGENT_DECISION_IDENTITY")

    with db.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("agent-paper:" + ticker,))
        prior = _existing_intent(conn, decision_id)
        if prior:
            return prior

        classification, blockers = _report_classification(conn, decision)
        if decision.get("status") != "RECORDED":
            blockers = list(blockers) + ["AGENT_DECISION_NOT_RECORDED"]
        price_date, price = _latest_price(conn, ticker)
        if price is None:
            blockers = list(blockers) + ["LATEST_COMPLETED_PRICE_UNAVAILABLE"]

        current = _open_position(conn, ticker)
        desired = desired_side(classification)
        action = deterministic_action(desired, current["side"] if current else None)
        state = _portfolio_state(conn, require_complete_prices=False)
        target = 0.0
        if action in {"OPEN_LONG", "OPEN_SHORT"} and not blockers:
            target, risk_blockers = position_budget(state["equity_usd"], state["gross_exposure_usd"], state["open_count"])
            blockers.extend(risk_blockers)

        if blockers:
            action = "NO_TRADE"
            desired = "FLAT" if classification is None else desired
            status = "BLOCKED"
            rationale = "Paper intent withheld because deterministic prerequisites or portfolio limits failed."
        elif action == "HOLD":
            status = "NO_ACTION"
            rationale = "Existing paper position already matches the exact source classification."
        elif action == "NO_TRADE":
            status = "NO_ACTION"
            rationale = "Exact source classification does not call for an open paper position."
        elif action == "CLOSE":
            status = "APPROVED"
            rationale = "Close the existing paper position; reversals require a later recorded decision."
        else:
            status = "APPROVED"
            rationale = "Open a bounded paper position from the exact source classification and deterministic risk budget."

        intent_id = str(uuid4())
        risk = {"starting_equity_usd": STARTING_EQUITY_USD,
                "equity_before_usd": state["equity_usd"], "gross_before_usd": state["gross_exposure_usd"],
                "open_positions_before": state["open_count"], "max_position_pct": MAX_POSITION_PCT * 100,
                "max_gross_pct": MAX_GROSS_PCT * 100, "max_open_positions": MAX_OPEN_POSITIONS,
                "fill_cost_bps": FILL_COST_BPS, "broker_submission": False}
        conn.execute(
            """INSERT INTO agent_trade_intents(intent_id,decision_id,ticker,classification,desired_side,action,status,
                   market_date,reference_price,target_notional_usd,rationale,blockers,risk_snapshot)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (intent_id, decision_id, ticker, classification, desired, action, status, price_date, price,
             target, rationale, Jsonb(blockers), Jsonb(risk)),
        )

        if status == "APPROVED" and action in {"OPEN_LONG", "OPEN_SHORT"}:
            side = "LONG" if action == "OPEN_LONG" else "SHORT"
            units = target / float(price)
            cost = target * FILL_COST_BPS / 10_000.0
            position_id, fill_id = str(uuid4()), str(uuid4())
            conn.execute(
                """INSERT INTO agent_paper_positions(position_id,ticker,side,source_decision_id,source_intent_id,
                       entry_market_date,entry_price,paper_units,entry_notional_usd,entry_cost_usd)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (position_id,ticker,side,decision_id,intent_id,price_date,price,units,target,cost),
            )
            conn.execute(
                """INSERT INTO agent_paper_fills(fill_id,intent_id,position_id,ticker,fill_kind,side,market_date,
                       price,paper_units,notional_usd,cost_usd) VALUES (%s,%s,%s,%s,'OPEN',%s,%s,%s,%s,%s,%s)""",
                (fill_id,intent_id,position_id,ticker,side,price_date,price,units,target,cost),
            )
            conn.execute("UPDATE agent_trade_intents SET status='SIMULATED' WHERE intent_id=%s", (intent_id,))
            status = "SIMULATED"
        elif status == "APPROVED" and action == "CLOSE" and current:
            units = float(current["paper_units"])
            notional = units * float(price)
            exit_cost = notional * FILL_COST_BPS / 10_000.0
            sign = 1.0 if current["side"] == "LONG" else -1.0
            pnl = sign * (float(price) - float(current["entry_price"])) * units - float(current["entry_cost_usd"]) - exit_cost
            fill_id = str(uuid4())
            conn.execute(
                """UPDATE agent_paper_positions SET status='CLOSED',exit_market_date=%s,exit_price=%s,
                       exit_cost_usd=%s,realized_pnl_usd=%s,exit_reason=%s,updated_at=now() WHERE position_id=%s""",
                (price_date,price,exit_cost,pnl,"deterministic agent signal changed",current["position_id"]),
            )
            conn.execute(
                """INSERT INTO agent_paper_fills(fill_id,intent_id,position_id,ticker,fill_kind,side,market_date,
                       price,paper_units,notional_usd,cost_usd) VALUES (%s,%s,%s,%s,'CLOSE',%s,%s,%s,%s,%s,%s)""",
                (fill_id,intent_id,current["position_id"],ticker,current["side"],price_date,price,units,notional,exit_cost),
            )
            conn.execute("UPDATE agent_trade_intents SET status='SIMULATED' WHERE intent_id=%s", (intent_id,))
            status = "SIMULATED"

        return {"intent_id": intent_id, "ticker": ticker, "classification": classification,
                "desired_side": desired, "action": action, "status": status,
                "market_date": price_date, "reference_price": price,
                "target_notional_usd": target, "rationale": rationale, "blockers": blockers,
                "risk_snapshot": risk, "replayed": False, "execution_mode": "PAPER",
                "broker_submission": False}
