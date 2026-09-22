"""Shared research -> journal -> fail-closed v2 -> PAPER -> mark cycle."""
from __future__ import annotations
from collections.abc import Callable
from .agent_intelligence.instructions import paper_instruction

COMPLETED_RESEARCH = {"COMPLETED", "COMPLETED_WITH_WITHHELD_OUTPUTS"}


def run(ticker: str, idempotency_key: str, *, capture_guard: Callable[[], None] | None = None) -> dict:
    ticker, key = str(ticker or "").strip().upper(), str(idempotency_key or "").strip()
    if not ticker or not key:
        raise ValueError("AGENT_CYCLE_IDENTITY_REQUIRED")
    from .research_cycle import run as research
    cycle = research(ticker, key)
    if cycle.get("status") == "BUSY":
        return {"status": "BUSY", "ticker": ticker, "idempotency_key": key,
                "broker_submission": False, "trade_execution": False}
    if cycle.get("status") not in COMPLETED_RESEARCH:
        raise RuntimeError("RESEARCH_NOT_COMPLETED")
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
    try:
        from .agent_intelligence.orchestrator import evaluate_decision
        intelligence = evaluate_decision(decision, mode="PAPER" if mode.get("mode") == "PAPER" else "SHADOW")
    except Exception as exc:
        intelligence = {"schema_version": "agent-intelligence.v2", "status": "UNAVAILABLE",
                        "reason": f"{type(exc).__name__}: intelligence evaluation failed",
                        "paper_instruction": None, "broker_submission": False}
    instruction = paper_instruction(intelligence, mode.get("mode"))
    trading = agent_trading.process_decision(decision, paper_instruction=instruction)
    paper_mark = None
    if trading.get("execution_mode") == "PAPER":
        try:
            paper_mark = agent_trading.mark_open_positions()
        except ValueError as exc:
            paper_mark = {"status": "BLOCKED", "reason": str(exc), "broker_submission": False}
    return {"status": cycle["status"], "ticker": ticker, "idempotency_key": key,
            "research_replayed": bool(cycle.get("replayed")), "journal_replayed": bool(saved.get("replayed")),
            "provider_status": cycle.get("provider_status"), "news_status": cycle.get("news_status"),
            "claims_verified": cycle.get("claims_verified"), "report_id": cycle.get("report_id"),
            "decision_id": decision["decision_id"], "decision_status": decision["status"], "action": decision["action"],
            "price_date": decision.get("price_date"), "blockers": decision.get("blockers", []),
            "intelligence_v2": intelligence, "trading": trading, "paper_mark": paper_mark,
            "trade_execution": trading.get("status") == "SIMULATED", "broker_submission": False}
