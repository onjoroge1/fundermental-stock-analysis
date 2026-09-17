"""Resumable, owner-operated five-stock runs over existing research services."""
from __future__ import annotations

from . import store
from .security import PanelError


def run_step(run_id: str):
    item = store.claim(run_id)
    if item is None:
        return {"status": "NO_RUNNABLE_ITEM", "run_id": run_id}
    try:
        from ..research_cycle import run as research
        from ..agents import journal
        from ..agents.contracts import CaptureRequest
        from .. import agent_trading
        ticker = item["ticker"]
        key = "panel:" + run_id
        store.require_capture_enabled()
        cycle = research(ticker, key)
        if cycle.get("status") == "BUSY":
            raise PanelError("RESEARCH_ALREADY_RUNNING", 409)
        if cycle.get("status") not in {"COMPLETED", "COMPLETED_WITH_WITHHELD_OUTPUTS"}:
            raise PanelError("RESEARCH_NOT_COMPLETED", 503)
        # A pause during a source read prevents a NEW immutable capture.
        store.require_capture_enabled()
        saved = journal.capture(ticker, CaptureRequest(idempotency_key=key))
        decision = saved["decision"]
        if decision.get("mode") != "RESEARCH" or decision.get("execution_status") != "NOT_ENABLED":
            raise PanelError("RESEARCH_BOUNDARY_VIOLATION", 503)

        mode = agent_trading.get_mode()
        trading = (agent_trading.process_decision(decision) if mode["mode"] == "PAPER"
                   else {"status": "SKIPPED", "execution_mode": "RESEARCH",
                         "broker_submission": False, "reason": "PAPER_MODE_NOT_ENABLED"})
        result = {"provider_status": cycle.get("provider_status"),
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
                  "broker_submission": False}
        return store.finish(item, result, failed=decision["status"] == "FAILED")
    except Exception as exc:
        code = exc.code if isinstance(exc, PanelError) else "RESEARCH_STEP_FAILED"
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
