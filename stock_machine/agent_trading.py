"""Agent Trading v1: decision-linked deterministic paper execution.

The research agent never talks to a broker. A recorded research decision may be
translated into an equity-only paper intent by fixed rules and fixed portfolio
risk limits. All fills are simulated from the latest completed-session adjusted
close and are explicitly marked simulated.

The paper ledger is intentionally self-contained. Its tables are created only by
an authenticated owner write that switches the system into PAPER mode (or by a
paper write after that); normal reads never run DDL. This keeps Agent Trading v1
out of the application's migration/release critical path.
"""
from __future__ import annotations

from uuid import uuid4

import psycopg
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

PAPER_SELECTOR_VERSION = "fundamental-score-paper-v1"
PAPER_LONG_MIN_SCORE = 70.0
PAPER_SHORT_MAX_SCORE = 50.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_trading_settings (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
    mode TEXT NOT NULL DEFAULT 'RESEARCH' CHECK(mode IN ('RESEARCH','PAPER')),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO agent_trading_settings(singleton) VALUES (true)
ON CONFLICT (singleton) DO NOTHING;
CREATE TABLE IF NOT EXISTS agent_trade_intents (
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
    UNIQUE(decision_id)
);
CREATE INDEX IF NOT EXISTS agent_trade_intents_ticker_created
    ON agent_trade_intents(ticker,created_at DESC);
CREATE TABLE IF NOT EXISTS agent_paper_positions (
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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS agent_paper_one_open_per_ticker
    ON agent_paper_positions(ticker) WHERE status='OPEN';
CREATE TABLE IF NOT EXISTS agent_paper_fills (
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_paper_marks (
    position_id UUID NOT NULL REFERENCES agent_paper_positions(position_id),
    market_date DATE NOT NULL,
    price DOUBLE PRECISION NOT NULL CHECK(price > 0),
    unrealized_pnl_usd DOUBLE PRECISION NOT NULL,
    gross_notional_usd DOUBLE PRECISION NOT NULL CHECK(gross_notional_usd >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(position_id,market_date)
);
"""


def ensure_schema(conn) -> None:
    """Initialize only the isolated paper ledger; never modify core app schema."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()


def _default_mode(*, initialized: bool) -> dict:
    return {"mode": "RESEARCH", "version": 0 if not initialized else 1,
            "updated_at": None, "initialized": initialized,
            "broker_submission": False, "live_trading_available": False}


def get_mode() -> dict:
    """Read mode without creating tables. Missing ledger means RESEARCH."""
    with db.connect() as conn:
        try:
            row = conn.execute(
                "SELECT mode,version,updated_at::text FROM agent_trading_settings WHERE singleton"
            ).fetchone()
        except psycopg.errors.UndefinedTable:
            conn.rollback()
            return _default_mode(initialized=False)
    if not row:
        return _default_mode(initialized=False)
    return {"mode": row[0], "version": row[1], "updated_at": row[2],
            "initialized": True, "broker_submission": False,
            "live_trading_available": False}


def set_mode(mode: str, expected_version: int | None = None) -> dict:
    mode = str(mode or "").upper()
    if mode not in {"RESEARCH", "PAPER"}:
        raise ValueError("TRADING_MODE_NOT_SUPPORTED")
    with db.connect() as conn:
        ensure_schema(conn)
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('agent-trading-mode',0))")
        row = conn.execute(
            "SELECT mode,version FROM agent_trading_settings WHERE singleton FOR UPDATE"
        ).fetchone()
        if not row:
            raise ValueError("TRADING_SETTINGS_UNAVAILABLE")
        if expected_version is not None and int(expected_version) != int(row[1]):
            raise ValueError("TRADING_SETTINGS_CHANGED_RELOAD")
        if row[0] != mode:
            conn.execute(
                "UPDATE agent_trading_settings SET mode=%s,version=version+1,updated_at=now() WHERE singleton",
                (mode,),
            )
        result = conn.execute(
            "SELECT mode,version,updated_at::text FROM agent_trading_settings WHERE singleton"
        ).fetchone()
    return {"mode": result[0], "version": result[1], "updated_at": result[2],
            "initialized": True, "broker_submission": False,
            "live_trading_available": False}


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


def _paper_experiment_signal(conn, decision: dict) -> dict:
    """Paper-only directional selector from the exact frozen decision packet.

    This is deliberately not a research recommendation or qualified forecast.
    It exists so the paper ledger can accumulate prospective outcomes while
    report guidance remains withheld. Thresholds are fixed and versioned.
    """
    input_sha = decision.get("input_sha256")
    if not input_sha:
        return {"version": PAPER_SELECTOR_VERSION, "desired_side": "FLAT",
                "classification": "PAPER_EXPERIMENT_UNAVAILABLE",
                "score": None, "blockers": ["FROZEN_EVIDENCE_ID_MISSING"]}
    row = conn.execute(
        "SELECT payload FROM agent_lab_evidence WHERE input_sha256=%s",
        (input_sha,),
    ).fetchone()
    if not row:
        return {"version": PAPER_SELECTOR_VERSION, "desired_side": "FLAT",
                "classification": "PAPER_EXPERIMENT_UNAVAILABLE",
                "score": None, "blockers": ["FROZEN_EVIDENCE_NOT_FOUND"]}
    packet = row[0] or {}
    quality = packet.get("data_quality") or {}
    if quality.get("status") != "PASS":
        return {"version": PAPER_SELECTOR_VERSION, "desired_side": "FLAT",
                "classification": "PAPER_EXPERIMENT_UNAVAILABLE",
                "score": None, "blockers": ["PAPER_EXPERIMENT_DATA_QUALITY_NOT_PASS"]}
    score = (((packet.get("fundamentals") or {}).get("fundamental_scores") or {})
             .get("composite_score"))
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return {"version": PAPER_SELECTOR_VERSION, "desired_side": "FLAT",
                "classification": "PAPER_EXPERIMENT_UNAVAILABLE",
                "score": None, "blockers": ["PAPER_EXPERIMENT_SCORE_UNAVAILABLE"]}
    score = float(score)
    if score >= PAPER_LONG_MIN_SCORE:
        desired, label = "LONG", "PAPER_EXPERIMENT_LONG"
    elif score <= PAPER_SHORT_MAX_SCORE:
        desired, label = "SHORT", "PAPER_EXPERIMENT_SHORT"
    else:
        desired, label = "FLAT", "PAPER_EXPERIMENT_FLAT"
    return {"version": PAPER_SELECTOR_VERSION, "desired_side": desired,
            "classification": label, "score": score, "blockers": [],
            "thresholds": {"long_min": PAPER_LONG_MIN_SCORE,
                           "short_max": PAPER_SHORT_MAX_SCORE}}


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
                              "entry_price": float(entry), "paper_units": float(units),
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
    return {"status": "OK", "starting_equity_usd": STARTING_EQUITY_USD,
            "equity_usd": round(equity, 2), "realized_pnl_usd": round(realized, 2),
            "unrealized_pnl_usd": round(unrealized, 2), "gross_exposure_usd": round(gross, 2),
            "gross_exposure_pct": round((gross / equity * 100), 2) if equity > 0 else None,
            "open_count": len(rows), "positions": open_rows, "missing_prices": missing,
            "broker_submission": False,
            "limits": {"max_position_pct": MAX_POSITION_PCT * 100,
                       "max_gross_pct": MAX_GROSS_PCT * 100,
                       "max_open_positions": MAX_OPEN_POSITIONS,
                       "fill_cost_bps": FILL_COST_BPS}}


def portfolio() -> dict:
    mode = get_mode()
    if not mode["initialized"]:
        return {"status": "NOT_INITIALIZED", "starting_equity_usd": STARTING_EQUITY_USD,
                "equity_usd": STARTING_EQUITY_USD, "realized_pnl_usd": 0.0,
                "unrealized_pnl_usd": 0.0, "gross_exposure_usd": 0.0,
                "gross_exposure_pct": 0.0, "open_count": 0, "positions": [],
                "missing_prices": [], "broker_submission": False,
                "limits": {"max_position_pct": MAX_POSITION_PCT * 100,
                           "max_gross_pct": MAX_GROSS_PCT * 100,
                           "max_open_positions": MAX_OPEN_POSITIONS,
                           "fill_cost_bps": FILL_COST_BPS}}
    with db.connect() as conn:
        return _portfolio_state(conn)


def mark_open_positions() -> dict:
    mode = get_mode()
    if mode["mode"] != "PAPER":
        raise ValueError("PAPER_MODE_REQUIRED")
    with db.connect() as conn:
        ensure_schema(conn)
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
    return {**dict(zip(cols, row)), "replayed": True, "execution_mode": "PAPER",
            "broker_submission": False}


def process_decision(decision: dict) -> dict:
    """Create at most one paper intent/fill sequence for one frozen agent decision."""
    ticker = str(decision.get("ticker") or "").upper()
    decision_id = str(decision.get("decision_id") or "")
    if ticker not in PILOT or not decision_id:
        raise ValueError("INVALID_AGENT_DECISION_IDENTITY")
    if get_mode()["mode"] != "PAPER":
        return {"status": "SKIPPED", "ticker": ticker, "execution_mode": "RESEARCH",
                "broker_submission": False, "reason": "PAPER_MODE_NOT_ENABLED"}

    with db.connect() as conn:
        ensure_schema(conn)
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("agent-paper:" + ticker,))
        prior = _existing_intent(conn, decision_id)
        if prior:
            return prior

        report_classification, blockers = _report_classification(conn, decision)
        if decision.get("status") != "RECORDED":
            blockers = list(blockers) + ["AGENT_DECISION_NOT_RECORDED"]

        selector = {"version": "source-report-classification",
                    "classification": report_classification,
                    "desired_side": desired_side(report_classification)}
        classification = report_classification
        desired = desired_side(report_classification)
        if (not blockers and decision.get("status") == "RECORDED"
                and report_classification == "INSUFFICIENT_DATA"):
            selector = _paper_experiment_signal(conn, decision)
            blockers = list(blockers) + list(selector.get("blockers") or [])
            classification = selector.get("classification")
            desired = selector.get("desired_side") or "FLAT"

        price_date, price = _latest_price(conn, ticker)
        if price is None:
            blockers = list(blockers) + ["LATEST_COMPLETED_PRICE_UNAVAILABLE"]

        current = _open_position(conn, ticker)
        action = deterministic_action(desired, current["side"] if current else None)
        state = _portfolio_state(conn, require_complete_prices=False)
        target = 0.0
        if action in {"OPEN_LONG", "OPEN_SHORT"} and state["missing_prices"]:
            blockers = list(blockers) + ["PORTFOLIO_MARK_INCOMPLETE"]
        if action in {"OPEN_LONG", "OPEN_SHORT"} and not blockers:
            target, risk_blockers = position_budget(
                state["equity_usd"], state["gross_exposure_usd"], state["open_count"]
            )
            blockers.extend(risk_blockers)

        if blockers:
            action = "NO_TRADE"
            desired = "FLAT" if classification is None else desired
            status = "BLOCKED"
            rationale = "Paper intent withheld because deterministic prerequisites or portfolio limits failed."
        elif action == "HOLD":
            status = "NO_ACTION"
            rationale = "Existing paper position already matches the deterministic paper selector."
        elif action == "NO_TRADE":
            status = "NO_ACTION"
            rationale = "Deterministic paper selector is flat; no simulated position is opened."
        elif action == "CLOSE":
            status = "APPROVED"
            rationale = "Close the existing paper position; selector reversals require a later recorded decision."
        else:
            status = "APPROVED"
            rationale = "Open a bounded experimental paper position from frozen evidence and deterministic risk limits; this is not a research recommendation."

        intent_id = str(uuid4())
        risk = {"starting_equity_usd": STARTING_EQUITY_USD,
                "equity_before_usd": state["equity_usd"],
                "gross_before_usd": state["gross_exposure_usd"],
                "open_positions_before": state["open_count"],
                "max_position_pct": MAX_POSITION_PCT * 100,
                "max_gross_pct": MAX_GROSS_PCT * 100,
                "max_open_positions": MAX_OPEN_POSITIONS,
                "fill_cost_bps": FILL_COST_BPS, "broker_submission": False,
                "selector": selector,
                "source_report_classification": report_classification}
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
            pnl = (sign * (float(price) - float(current["entry_price"])) * units
                   - float(current["entry_cost_usd"]) - exit_cost)
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
                "target_notional_usd": target, "rationale": rationale,
                "blockers": blockers, "risk_snapshot": risk, "replayed": False,
                "execution_mode": "PAPER", "broker_submission": False}
