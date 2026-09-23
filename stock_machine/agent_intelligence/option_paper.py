"""Append-only simulated option entries and expiry outcomes for v2.

Entries use the exact conservative natural prices embedded in an eligible
StrategyCandidate. They are simulations, never broker fills.
"""
from __future__ import annotations

from .. import db, research_store
from ..options.models import OptionLeg
from ..options.payoff import expiration_pnl


def open_entry(ticker: str, decision_id: str, candidate: dict) -> dict:
    with db.connect() as conn:
        existing = research_store.get(conn, "AGENT_OPTION_PAPER_V1", decision_id)
        if existing:
            return {"replayed": True, **existing["payload"],
                    "record_id": existing["record_id"]}
    payoff = candidate.get("payoff") or {}
    if not payoff.get("defined_risk") or payoff.get("max_loss") is None:
        raise ValueError("OPTION_PAPER_DEFINED_RISK_REQUIRED")
    payload = {
        "schema_version": "agent-option-paper.v1",
        "status": "SIMULATED_ENTRY",
        "ticker": ticker,
        "decision_id": decision_id,
        "candidate_id": candidate.get("candidate_id"),
        "strategy_type": candidate.get("strategy_type"),
        "expiration": candidate.get("expiration"),
        "spot_price": candidate.get("spot_price"),
        "legs": candidate.get("legs") or [],
        "payoff": payoff,
        "entry_basis": "candidate natural prices; buy at ask / sell at bid",
        "broker_submission": False,
    }
    with db.connect() as conn:
        saved = research_store.save(conn, "AGENT_OPTION_PAPER_V1",
                                    decision_id, payload, ticker)
    return {"replayed": False, **payload, "record_id": saved["record_id"]}


def settle_if_matured(ticker: str, decision_id: str) -> dict:
    with db.connect() as conn:
        existing = research_store.get(conn, "AGENT_OPTION_PAPER_OUTCOME_V1", decision_id)
        if existing:
            return {"replayed": True, **existing["payload"]}
        entry = research_store.get(conn, "AGENT_OPTION_PAPER_V1", decision_id)
        if not entry:
            raise ValueError("OPTION_PAPER_ENTRY_NOT_FOUND")
        payload = entry["payload"]
        expiration = payload.get("expiration")
        if not expiration:
            raise ValueError("OPTION_PAPER_EXPIRATION_MISSING")
        row = conn.execute(
            """SELECT COALESCE(adj_close,close) FROM prices_daily
               WHERE ticker=%s AND date=%s""", (ticker, expiration)
        ).fetchone()
        if not row:
            return {"status": "PENDING_MATURITY", "ticker": ticker,
                    "decision_id": decision_id, "expiration": expiration,
                    "broker_submission": False}
        underlying = float(row[0])
        legs = [OptionLeg.model_validate(x) for x in payload.get("legs") or []]
        pnl = float(expiration_pnl(legs, underlying))
        max_loss = float((payload.get("payoff") or {}).get("max_loss") or 0)
        outcome = {
            "schema_version": "agent-option-paper-outcome.v1",
            "status": "MATURED",
            "ticker": ticker,
            "decision_id": decision_id,
            "candidate_id": payload.get("candidate_id"),
            "strategy_type": payload.get("strategy_type"),
            "expiration": expiration,
            "underlying_expiration_price": underlying,
            "expiration_pnl_usd": round(pnl, 2),
            "return_on_max_risk_pct": (round(pnl / max_loss * 100, 4)
                                       if max_loss > 0 else None),
            "learning_status": "OUTCOME_RECORDED_PATH_DRAWDOWN_NOT_AVAILABLE",
            "broker_submission": False,
        }
        research_store.save(conn, "AGENT_OPTION_PAPER_OUTCOME_V1",
                            decision_id, outcome, ticker)
    return {"replayed": False, **outcome}
