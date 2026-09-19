"""Resumable, owner-operated five-stock runs over existing research services."""
from __future__ import annotations

from . import store
from .security import PanelError


def run_step(run_id: str):
    item = store.claim(run_id)
    if item is None:
        return {"status": "NO_RUNNABLE_ITEM", "run_id": run_id}
    try:
        from ..agent_cycle import run as agent_cycle
        ticker = item["ticker"]
        key = "panel:" + run_id
        # Avoid paid/provider work when the owner has already paused research.
        store.require_capture_enabled()
        result = agent_cycle(ticker, key, capture_guard=store.require_capture_enabled)
        if result.get("status") == "BUSY":
            raise PanelError("RESEARCH_ALREADY_RUNNING", 409)
        return store.finish(
            item, result, failed=result.get("decision_status") == "FAILED"
        )
    except Exception as exc:
        if isinstance(exc, PanelError):
            code = exc.code
        elif str(exc) in {"RESEARCH_NOT_COMPLETED", "RESEARCH_BOUNDARY_VIOLATION"}:
            code = str(exc)
        else:
            code = "RESEARCH_STEP_FAILED"
        return store.finish(item, {"error_code": code, "trade_execution": False,
                                   "broker_submission": False}, failed=True)


def trading_summary():
    from .. import agent_trading
    mode = agent_trading.get_mode()
    paper = agent_trading.portfolio()
    return {"mode": mode, "paper": paper, "broker_submission": False,
            "live_trading_available": False,
            "note": "PAPER uses deterministic simulated fills only. No broker order path exists in Agent Trading v1."}


def connection_summary():
    from .. import research_store
    from ..agents.contracts import PILOT
    from ..ingestion.massive import configured
    with store.connect() as conn:
        values = []
        for ticker in PILOT:
            record = research_store.latest(conn, "PROVIDER_DAILY", ticker)
            value = record.get("payload", {}) if record else {}
            values.append({"ticker": ticker, "status": value.get("status", "NOT_TESTED"),
                           "market_date": value.get("market_date"), "observed_at": value.get("observed_at"),
                           "reason": value.get("reason")})
    return {"massive": {"configured": configured(), "observations": values,
                        "note": "Configured is not authenticated or entitled. Run pilot performs bounded checks."},
            "ibkr": {"status": "NOT_USED_BY_AGENT_TRADING_V1",
                     "note": "Agent Trading v1 has no broker order-submission capability."}}
