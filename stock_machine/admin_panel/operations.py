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


def _latest_run_intelligence(conn):
    """Recover v2 outcomes already committed with completed pilot items."""
    try:
        rows = conn.execute(
            """SELECT DISTINCT ON (ticker) ticker,result,updated_at::text
               FROM operator_pilot_items
               WHERE status='COMPLETED'
                 AND jsonb_typeof(result->'intelligence_v2')='object'
               ORDER BY ticker,updated_at DESC"""
        ).fetchall()
    except (AttributeError, TypeError):
        # Lightweight test/read adapters may expose only research_store reads.
        return {}
    return {
        ticker: {
            "payload": result["intelligence_v2"],
            "recorded_at": updated_at,
            "source": "PILOT_RUN_RESULT",
        }
        for ticker, result, updated_at in rows
        if isinstance(result, dict) and isinstance(result.get("intelligence_v2"), dict)
    }


def _newest(*records):
    values = [record for record in records if record]
    return max(values, key=lambda record: record.get("recorded_at") or "") if values else None


def _learning_loop_health(conn) -> dict:
    """Read-only evidence for the scheduler → cycle → reward loop."""
    try:
        rows = conn.execute(
            """SELECT DISTINCT ON (job_type,COALESCE(ticker,''))
                      job_type,ticker,status,created_at::text,finished_at::text,
                      attempts,last_error,result
               FROM orchestration_jobs
               WHERE job_type IN ('research_cycle','agent_intelligence_outcomes')
               ORDER BY job_type,COALESCE(ticker,''),created_at DESC"""
        ).fetchall()
    except Exception:
        return {
            "status": "UNAVAILABLE",
            "cron_schedule": "hourly",
            "outcome_scan_window_utc": "22:00 or later",
            "research_cycles": [],
            "latest_outcome_scan": None,
        }
    if any(len(row) < 8 for row in rows):
        return {
            "status": "UNAVAILABLE",
            "cron_schedule": "hourly",
            "outcome_scan_window_utc": "22:00 or later",
            "research_cycles": [],
            "latest_outcome_scan": None,
        }
    values = [{
        "job_type": row[0], "ticker": row[1], "status": row[2],
        "created_at": row[3], "finished_at": row[4], "attempts": row[5],
        "last_error": (str(row[6])[:300] if row[6] else None),
        "result_summary": ({
            "scored": (row[7] or {}).get("scored"),
            "pending": (row[7] or {}).get("pending"),
        } if row[0] == "agent_intelligence_outcomes" and isinstance(row[7], dict) else None),
    } for row in rows]
    cycles = sorted(
        (value for value in values if value["job_type"] == "research_cycle"),
        key=lambda value: value.get("ticker") or "",
    )
    outcome = next((value for value in values
                    if value["job_type"] == "agent_intelligence_outcomes"), None)
    statuses = [value["status"] for value in values]
    if any(status == "FAILED" for status in statuses):
        status = "ATTENTION"
    elif cycles or outcome:
        status = "ACTIVE"
    else:
        status = "NO_EVIDENCE"
    return {
        "status": status,
        "cron_schedule": "hourly",
        "outcome_scan_window_utc": "22:00 or later",
        "research_cycles": cycles,
        "latest_outcome_scan": outcome,
    }


def intelligence_summary():
    """Owner-facing latest Agent Intelligence v2 state for the pilot."""
    from .. import research_store
    from ..agents.contracts import PILOT
    from ..agent_intelligence.outcomes import HORIZON_SESSIONS
    from ..market_calendar import latest_completed_session, session_offset
    completed_session = str(latest_completed_session())
    rows = []
    with store.connect() as conn:
        loop_health = _learning_loop_health(conn)
        run_records = _latest_run_intelligence(conn)
        for ticker in PILOT:
            success = research_store.latest(conn, "AGENT_INTELLIGENCE_V2", ticker)
            failure = research_store.latest(conn, "AGENT_INTELLIGENCE_V2_FAILURE", ticker)
            run_record = run_records.get(ticker)
            if run_record:
                run_status = (run_record.get("payload") or {}).get("status")
                if run_status == "UNAVAILABLE":
                    failure = _newest(failure, run_record)
                else:
                    success = _newest(success, run_record)
            latest = _newest(success, failure)
            if failure and latest is failure:
                failed = failure.get("payload") or {}
                rows.append({
                    "ticker": ticker,
                    "status": "UNAVAILABLE",
                    "mode": failed.get("mode"),
                    "decision_id": failed.get("decision_id"),
                    "reason_code": failed.get("reason_code") or "INTELLIGENCE_EVALUATION_FAILED",
                    "recorded_at": failure.get("recorded_at"),
                })
                continue
            if not success:
                rows.append({"ticker": ticker, "status": "NOT_RUN"})
                continue
            value = success.get("payload") or {}
            state = value.get("state") or {}
            technical = state.get("technical") or {}
            news = state.get("news") or {}
            router = value.get("router") or {}
            bandit = value.get("bandit") or {}
            bandit_selected = bandit.get("selected") or {}
            selected = value.get("selected") or {}
            decision_id = value.get("decision_id")
            reward = (research_store.get(conn, "AGENT_REWARD_V2", str(decision_id))
                      if decision_id else None)
            reward_payload = (reward or {}).get("payload") or {}
            action = bandit_selected.get("action")
            entry_session = state.get("as_of")
            reward_due_session = None
            if entry_session and action in {"LONG_STOCK", "SHORT_STOCK", "NO_TRADE"}:
                reward_due_session = session_offset(entry_session, HORIZON_SESSIONS)
            if reward_payload:
                reward_status = "SCORED"
            elif str(action or "").startswith("OPTION:"):
                reward_status = "OPTION_PATH_REWARD_UNAVAILABLE"
            elif reward_due_session and completed_session >= reward_due_session:
                reward_status = "DUE_UNSCORED"
            elif reward_due_session:
                reward_status = "PENDING_MATURITY"
            else:
                reward_status = "NOT_APPLICABLE"
            arms = bandit.get("arms") or []
            eligible_actions = [arm.get("action") for arm in arms if arm.get("action")]
            if not eligible_actions:
                eligible_actions = [
                    candidate.get("action") for candidate in router.get("candidates") or []
                    if candidate.get("eligible") is True and not candidate.get("blockers")
                ]
            uncertainty = bandit_selected.get("uncertainty")
            alpha = bandit.get("alpha")
            exploration_bonus = bandit_selected.get("exploration_bonus")
            if exploration_bonus is None and uncertainty is not None and alpha is not None:
                exploration_bonus = float(uncertainty) * float(alpha)
            choice_driver = bandit.get("choice_driver")
            if not choice_driver and bandit_selected.get("observations") == 0:
                choice_driver = "EXPLORE_UNTRIED"
            rows.append({
                "ticker": ticker,
                "status": value.get("status") or "OK",
                "mode": value.get("mode"),
                "decision_id": decision_id,
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
                "bandit_selected": bandit_selected.get("action"),
                "bandit_ucb": bandit_selected.get("ucb"),
                "bandit_mean": bandit_selected.get("mean"),
                "bandit_uncertainty": uncertainty,
                "bandit_exploration_bonus": exploration_bonus,
                "bandit_observations": bandit_selected.get("observations"),
                "bandit_choice_driver": choice_driver,
                "eligible_actions": eligible_actions,
                "final_selected_action": selected.get("action"),
                "final_selected_instrument": selected.get("instrument"),
                "latest_reward": ((reward_payload.get("reward") or {}).get("reward")
                                  if reward_payload else None),
                "reward_status": reward_status,
                "reward_due_session": reward_due_session,
                "reward_horizon_sessions": HORIZON_SESSIONS,
                "recorded_at": success.get("recorded_at"),
            })
    return {
        "schema_version": "agent-intelligence-admin.v2",
        "rows": rows,
        "latest_completed_session": completed_session,
        "learning_loop": loop_health,
        "broker_submission": False,
        "note": "Latest v2 outcomes are reconciled from the evidence index and durable pilot-run results. Research mode is SHADOW; PAPER can simulate stock instructions only.",
    }
