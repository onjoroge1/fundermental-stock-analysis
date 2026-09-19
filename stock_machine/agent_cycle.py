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
    trading = agent_trading.process_decision(decision)

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
        "trading": trading,
        "trade_execution": trading.get("status") == "SIMULATED",
        "broker_submission": False,
    }
