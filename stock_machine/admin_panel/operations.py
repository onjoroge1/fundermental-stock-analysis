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


def intelligence_summary():
    """Owner-facing latest Agent Intelligence v2 state for the pilot."""
    from .. import research_store
    from ..agents.contracts import PILOT
    rows = []
    with store.connect() as conn:
        for ticker in PILOT:
            record = research_store.latest(conn, "AGENT_INTELLIGENCE_V2", ticker)
            reward = research_store.latest(conn, "AGENT_REWARD_V2", ticker)
            if not record:
                rows.append({"ticker": ticker, "status": "NOT_RUN"})
                continue
            value = record.get("payload") or {}
            state = value.get("state") or {}
            technical = state.get("technical") or {}
            news = state.get("news") or {}
            router = value.get("router") or {}
            bandit = value.get("bandit") or {}
            selected = value.get("selected") or {}
            reward_payload = (reward or {}).get("payload") or {}
            rows.append({
                "ticker": ticker,
                "status": "OK",
                "mode": value.get("mode"),
                "decision_id": value.get("decision_id"),
                "as_of": state.get("as_of"),
                "direction": state.get("direction"),
                "bias_score": state.get("bias_score"),
                "paper_eligible": state.get("paper_eligible"),
                "blockers": state.get("blockers") or [],
                "technical_trend": (technical.get("classification") or {}).get("trend"),
                "volatility_regime": (technical.get("classification") or {}).get("volatility_regime"),
                "news_pressure": (news.get("features") or {}).get("signed_event_pressure"),
                "news_events": (news.get("features") or {}).get("event_counts") or {},
                "option_surface_available": bool(state.get("option_surface")),
                "router_selected": (router.get("selected") or {}).get("action"),
                "router_instrument": (router.get("selected") or {}).get("instrument"),
                "router_strategy": (router.get("selected") or {}).get("strategy_type"),
                "bandit_selected": (bandit.get("selected") or {}).get("action"),
                "bandit_ucb": (bandit.get("selected") or {}).get("ucb"),
                "bandit_observations": (bandit.get("selected") or {}).get("observations"),
                "final_selected_action": selected.get("action"),
                "final_selected_instrument": selected.get("instrument"),
                "latest_reward": ((reward_payload.get("reward") or {}).get("reward")
                                  if reward_payload else None),
            })
    return {
        "schema_version": "agent-intelligence-admin.v1",
        "rows": rows,
        "broker_submission": False,
        "note": "Research mode is SHADOW. PAPER mode can simulate stock instructions only; option selections remain proposals until the options paper executor exists.",
    }
