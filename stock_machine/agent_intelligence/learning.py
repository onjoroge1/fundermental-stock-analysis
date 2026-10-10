"""Append-only outcome learning for Agent Intelligence v2."""

from __future__ import annotations

from . import bandit, reward

STATE_KIND = "AGENT_BANDIT_STATE_V2"
POOLED_SCOPE = "POOLED_COVERED_UNIVERSE"
POOLED_LOCK = "learning:pooled-bandit"


def current_arms(state: dict | None) -> dict:
    """Arms trained under the current reward and bandit contracts only.

    Rewards from an earlier contract have a different scale, and earlier
    per-ticker diagonal arms have a different shape; mixing either into the
    pooled model would corrupt it, so a contract change cold-starts.
    """
    state = state or {}
    if (
        state.get("scope") != POOLED_SCOPE
        or state.get("reward_version") != reward.VERSION
        or state.get("bandit_version") != bandit.VERSION
    ):
        return {}
    return dict(state.get("arms") or {})


def pooled_state(conn) -> dict | None:
    """Latest pooled state by its own sequence, not by transaction start time.

    recorded_at is the writing transaction's start time, so a writer that
    waited on the lock could otherwise appear older than the state it extended.
    """
    row = conn.execute(
        """SELECT payload FROM research_evidence_records
        WHERE kind=%s AND payload->>'scope'=%s AND payload->>'bandit_version'=%s
          AND payload->>'reward_version'=%s
        ORDER BY (payload->>'sequence')::bigint DESC LIMIT 1""",
        (STATE_KIND, POOLED_SCOPE, bandit.VERSION, reward.VERSION),
    ).fetchone()
    return row[0] if row else None


def record_outcome(ticker: str, decision_id: str, outcome: dict) -> dict:
    from .. import db, research_store
    from ..market_calendar import session_dates

    with db.connect() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (POOLED_LOCK,),
        )
        prior_reward = research_store.get(conn, "AGENT_REWARD_V3", decision_id)
        if prior_reward:
            return {
                "replayed": True,
                "reward_record": prior_reward["payload"],
                "bandit_state": pooled_state(conn),
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
        previous = pooled_state(conn)
        state = previous
        if action != bandit.BASELINE_ACTION:
            # Abstention is the known zero baseline: its reward record is kept
            # for audit, but there is no arm to estimate.
            arms = current_arms(previous)
            x = bandit.context_vector(payload["state"])
            arms[action] = bandit.update(arms.get(action) or bandit.empty_arm(len(x)), x, r["reward"])
            sequence = int((previous or {}).get("sequence") or 0) + 1
            state = {
                "scope": POOLED_SCOPE,
                "sequence": sequence,
                "bandit_version": bandit.VERSION,
                "reward_version": reward.VERSION,
                "source_ticker": ticker,
                "source_decision_id": decision_id,
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
        if state is not previous:
            research_store.save(
                conn, STATE_KIND, f"{POOLED_SCOPE}:{state['sequence']:012d}", state, None
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
