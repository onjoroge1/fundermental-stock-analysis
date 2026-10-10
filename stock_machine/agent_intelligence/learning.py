"""Append-only outcome learning for Agent Intelligence v2."""

from __future__ import annotations

from . import bandit, reward

STATE_KIND = "AGENT_BANDIT_STATE_V2"
POOLED_SCOPE = "POOLED_COVERED_UNIVERSE"
POOLED_LOCK = "learning:pooled-bandit"
LEARNING_BASIS = "PROSPECTIVE_COUNTERFACTUAL_V1"


def current_arms(state: dict | None) -> dict:
    """Arms trained under the current reward, bandit and learning contracts only.

    Rewards from an earlier contract have a different scale, and earlier
    per-ticker diagonal arms have a different shape; mixing either into the
    pooled model would corrupt it, so a contract change cold-starts.
    """
    state = state or {}
    if (
        state.get("scope") != POOLED_SCOPE
        or state.get("reward_version") != reward.VERSION
        or state.get("bandit_version") != bandit.VERSION
        or state.get("learning_basis") != LEARNING_BASIS
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
          AND payload->>'reward_version'=%s AND payload->>'learning_basis'=%s
        ORDER BY (payload->>'sequence')::bigint DESC LIMIT 1""",
        (STATE_KIND, POOLED_SCOPE, bandit.VERSION, reward.VERSION, LEARNING_BASIS),
    ).fetchone()
    return row[0] if row else None


def record_counterfactual(ticker: str, decision_id: str, arm_outcomes: dict) -> dict:
    """Score every learned stock arm for one decision and update the pooled model.

    Idempotent per decision; the reward record and state advance commit together.
    """
    from .. import db, research_store
    from ..market_calendar import session_dates
    from .outcomes import CONTRACT, LEARNED_ARMS, OVERLAP_WEIGHT

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
        if payload.get("ticker") != ticker or payload.get("learning_contract") != CONTRACT:
            raise ValueError("LEARNING_IDENTITY_OR_CONTRACT_INVALID")
        if not (payload.get("state") or {}).get("paper_eligible"):
            raise ValueError("LEARNING_STATE_NOT_ELIGIBLE")
        if set(arm_outcomes) != set(LEARNED_ARMS):
            raise ValueError("COUNTERFACTUAL_ARMS_INCOMPLETE")
        window = payload.get("learning") or {}
        features = ((payload.get("state") or {}).get("technical") or {}).get("features")
        x = bandit.context_vector(payload["state"])
        previous = pooled_state(conn)
        arms = current_arms(previous)
        scored = {}
        for action in LEARNED_ARMS:
            outcome = arm_outcomes[action]
            if (
                outcome.get("learning_basis") != "PROSPECTIVE_COUNTERFACTUAL_V1"
                or outcome.get("entry_date") != window.get("execution_session")
                or outcome.get("exit_date") != window.get("due_session")
            ):
                raise ValueError("COUNTERFACTUAL_WINDOW_MISMATCH")
            sessions = len(session_dates(outcome["entry_date"], outcome["exit_date"])) - 1
            scale = reward.risk_scale(features, sessions)
            r = reward.compute(
                gross_return_pct=float(outcome["gross_return_pct"]),
                max_drawdown_pct=float(outcome["max_drawdown_pct"]),
                capital_used_pct=float(outcome["capital_used_pct"]),
                turnover_pct=float(outcome.get("turnover_pct", 0.0)),
                costs_pct=float(outcome.get("costs_pct", 0.0)),
                risk_scale_pct=scale["risk_scale_pct"],
            )
            r["risk_scale"] = scale
            scored[action] = {"outcome": outcome, "reward": r}
            arms[action] = bandit.update(
                arms.get(action) or bandit.empty_arm(len(x)), x, r["reward"], OVERLAP_WEIGHT
            )
        sequence = int((previous or {}).get("sequence") or 0) + 1
        state = {
            "scope": POOLED_SCOPE,
            "sequence": sequence,
            "bandit_version": bandit.VERSION,
            "reward_version": reward.VERSION,
            "learning_basis": LEARNING_BASIS,
            "source_ticker": ticker,
            "source_decision_id": decision_id,
            "arms": arms,
        }
        record = {
            "ticker": ticker,
            "decision_id": decision_id,
            "learning_basis": LEARNING_BASIS,
            "learning_contract": CONTRACT,
            "selected_action": ((payload.get("bandit") or {}).get("selected") or {}).get("action"),
            "overlap_weight": OVERLAP_WEIGHT,
            "arms": scored,
        }
        research_store.save(conn, "AGENT_REWARD_V3", decision_id, record, ticker)
        research_store.save(
            conn, STATE_KIND, f"{POOLED_SCOPE}:{sequence:012d}", state, None
        )
    return {"replayed": False, "reward_record": record, "bandit_state": state}
