"""Shared end-to-end agent cycle: research -> immutable journal -> PAPER intent.

This module is the single orchestration path for manual owner runs and automated
maintenance. It never submits a broker order. Research and journal writes are
idempotent by request key; Agent Trading v1 is idempotent by decision_id.
"""
from __future__ import annotations

from collections.abc import Callable


COMPLETED_RESEARCH = {"COMPLETED", "COMPLETED_WITH_WITHHELD_OUTPUTS"}


def run(ticker: str, idempotency_key: str, *,
        capture_guard: Callable[[], None] | None = None) -> dict:
    ticker = str(ticker or "").strip().upper()
    key = str(idempotency_key or "").strip()
    if not ticker or not key:
        raise ValueError("AGENT_CYCLE_IDENTITY_REQUIRED")

    from .research_cycle import run as research
    cycle = research(ticker, key)
    if cycle.get("status") == "BUSY":
        return {"status": "BUSY", "ticker": ticker, "idempotency_key": key,
                "broker_submission": False, "trade_execution": False}
    if cycle.get("status") not in COMPLETED_RESEARCH:
        raise RuntimeError("RESEARCH_NOT_COMPLETED")

    # A manual/admin caller may use this to stop a new immutable capture if the
    # owner paused research while provider reads were in flight.
    if capture_guard is not None:
        capture_guard()

    from .agents import journal
    from .agents.contracts import CaptureRequest
    saved = journal.capture(ticker, CaptureRequest(idempotency_key=key))
    decision = saved["decision"]
    if decision.get("mode") != "RESEARCH" or decision.get("execution_status") != "NOT_ENABLED":
        raise RuntimeError("RESEARCH_BOUNDARY_VIOLATION")

    from . import agent_trading
    mode = agent_trading.get_mode()
    intelligence = None
    try:
        from .agent_intelligence.orchestrator import evaluate_decision
        intelligence = evaluate_decision(
            decision, mode="PAPER" if mode.get("mode") == "PAPER" else "SHADOW"
        )
    except Exception as exc:
        intelligence = {
            "schema_version": "agent-intelligence.v2",
            "status": "UNAVAILABLE",
            "reason": f"{type(exc).__name__}: intelligence evaluation failed",
            "paper_instruction": None,
            "broker_submission": False,
        }
    instruction = (intelligence or {}).get("paper_instruction") if mode.get("mode") == "PAPER" else None
    trading = agent_trading.process_decision(decision, paper_instruction=instruction)
    paper_mark = None
    if trading.get("execution_mode") == "PAPER":
        try:
            paper_mark = agent_trading.mark_open_positions()
        except ValueError as exc:
            # The immutable decision/intent remains valid even when a portfolio
            # mark is temporarily unavailable. Surface the mark blocker instead
            # of turning a completed research cycle into an ambiguous retry.
            paper_mark = {"status": "BLOCKED", "reason": str(exc),
                          "broker_submission": False}

    return {
        "status": cycle["status"],
        "ticker": ticker,
        "idempotency_key": key,
        "research_replayed": bool(cycle.get("replayed")),
        "journal_replayed": bool(saved.get("replayed")),
        "provider_status": cycle.get("provider_status"),
        "news_status": cycle.get("news_status"),
        "claims_verified": cycle.get("claims_verified"),
        "report_id": cycle.get("report_id"),
        "decision_id": decision["decision_id"],
        "decision_status": decision["status"],
        "action": decision["action"],
        "price_date": decision.get("price_date"),
        "blockers": decision.get("blockers", []),
        "intelligence_v2": intelligence,
        "trading": trading,
        "paper_mark": paper_mark,
        "trade_execution": trading.get("status") == "SIMULATED",
        "broker_submission": False,
    }
