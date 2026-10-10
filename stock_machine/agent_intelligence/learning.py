"""Append-only outcome learning for Agent Intelligence v2."""

from __future__ import annotations

from . import bandit, reward


def current_arms(latest: dict | None) -> dict:
    """Arms trained under the current reward contract only.

    Rewards from an earlier contract have a different scale; mixing them into
    one arm would corrupt its estimate, so a contract change cold-starts.
    """
    payload = (latest or {}).get("payload") or {}
    if payload.get("reward_version") != reward.VERSION:
        return {}
    return dict(payload.get("arms") or {})


def record_outcome(ticker: str, decision_id: str, outcome: dict) -> dict:
    from .. import db, research_store
    from ..market_calendar import session_dates

    with db.connect() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("learning:" + ticker,),
        )
        prior_reward = research_store.get(conn, "AGENT_REWARD_V3", decision_id)
        if prior_reward:
            latest = research_store.latest(conn, "AGENT_BANDIT_STATE_V2", ticker)
            return {
                "replayed": True,
                "reward_record": prior_reward["payload"],
                "bandit_state": (latest or {}).get("payload"),
            }
        run = research_store.get(conn, "AGENT_INTELLIGENCE_V2", decision_id)
        if not run:
            raise ValueError("INTELLIGENCE_RUN_NOT_FOUND")
        payload = run["payload"]
        if (
            payload.get("ticker") != ticker
            or payload.get("learning_contract") != "executed-paper.v3"
        ):
            raise ValueError("LEARNING_IDENTITY_OR_CONTRACT_INVALID")
        if outcome.get("learning_basis") not in {
            "REALIZED_PAPER_FILL_V1",
            "PAPER_ABSTENTION_V1",
        }:
            raise ValueError("EXECUTION_BACKED_OUTCOME_REQUIRED")
        selected = (payload.get("bandit") or {}).get("selected") or {}
        action = selected.get("action")
        if not action:
            raise ValueError("INTELLIGENCE_ACTION_MISSING")
        sessions = len(session_dates(outcome["entry_date"], outcome["exit_date"])) - 1
        scale = reward.risk_scale(
            ((payload.get("state") or {}).get("technical") or {}).get("features"),
            sessions,
        )
        r = reward.compute(
            gross_return_pct=float(outcome["gross_return_pct"]),
            max_drawdown_pct=float(outcome["max_drawdown_pct"]),
            capital_used_pct=float(outcome["capital_used_pct"]),
            turnover_pct=float(outcome.get("turnover_pct", 0.0)),
            costs_pct=float(outcome.get("costs_pct", 0.0)),
            risk_scale_pct=scale["risk_scale_pct"],
        )
        r["risk_scale"] = scale
        latest = research_store.latest(conn, "AGENT_BANDIT_STATE_V2", ticker)
        arms = current_arms(latest)
        current = arms.get(action) or bandit.empty_arm()
        x = bandit.context_vector(payload["state"])
        arms[action] = bandit.update(current, x, r["reward"])
        state = {
            "ticker": ticker,
            "source_decision_id": decision_id,
            "reward_version": reward.VERSION,
            "arms": arms,
            "last_action": action,
            "last_reward": r,
        }
        research_store.save(
            conn,
            "AGENT_REWARD_V3",
            decision_id,
            {
                "ticker": ticker,
                "decision_id": decision_id,
                "action": action,
                "outcome": outcome,
                "reward": r,
            },
            ticker,
        )
        research_store.save(
            conn, "AGENT_BANDIT_STATE_V2", ticker + ":" + decision_id, state, ticker
        )
    return {
        "replayed": False,
        "reward_record": {
            "ticker": ticker,
            "decision_id": decision_id,
            "action": action,
            "outcome": outcome,
            "reward": r,
        },
        "bandit_state": state,
    }
