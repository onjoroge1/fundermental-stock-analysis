"""Append-only outcome learning for Agent Intelligence v2."""
from __future__ import annotations

from . import bandit, reward


def record_outcome(ticker: str, decision_id: str, outcome: dict) -> dict:
    from .. import db, research_store
    with db.connect() as conn:
        run=research_store.get(conn,"AGENT_INTELLIGENCE_V2",decision_id)
        if not run:
            raise ValueError("INTELLIGENCE_RUN_NOT_FOUND")
        payload=run["payload"]
        selected=(payload.get("bandit") or {}).get("selected") or {}
        action=selected.get("action")
        if not action:
            raise ValueError("INTELLIGENCE_ACTION_MISSING")
        r=reward.compute(
            net_return_pct=float(outcome["net_return_pct"]),
            max_drawdown_pct=float(outcome["max_drawdown_pct"]),
            capital_used_pct=float(outcome["capital_used_pct"]),
            turnover_pct=float(outcome.get("turnover_pct",0.0)),
            costs_pct=float(outcome.get("costs_pct",0.0)),
        )
        latest=research_store.latest(conn,"AGENT_BANDIT_STATE_V1",ticker)
        arms=dict((latest or {}).get("payload",{}).get("arms",{}))
        current=arms.get(action) or bandit.empty_arm()
        x=bandit.context_vector(payload["state"])
        arms[action]=bandit.update(current,x,r["reward"])
        state={"ticker":ticker,"source_decision_id":decision_id,
               "arms":arms,"last_action":action,"last_reward":r}
        research_store.save(conn,"AGENT_REWARD_V2",decision_id,
                            {"ticker":ticker,"decision_id":decision_id,
                             "action":action,"outcome":outcome,"reward":r},ticker)
        research_store.save(conn,"AGENT_BANDIT_STATE_V1",
                            ticker+":"+decision_id,state,ticker)
    return state
