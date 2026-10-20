"""Read-only views of the event strategies and of what the agent has learned.

For the API and the MCP connector. The strategies' signals are information
for a reader: nothing here is an input to the agent's state, bandit, paper or
live actions, and no strategy is qualified to trade until a registered
evaluation passes and is independently reviewed.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..backtest import event_replay as er
from . import fair_value_forward as fvf
from . import strategy_forward as sf
from . import strategy_signals as ss

RESULTS_PATH = Path(__file__).with_name("strategy_replay_results.json")
AGENT_USE = ("INFORMATION_ONLY: these signals are not inputs to the agent's bandit, paper or live actions; no strategy "
             "is qualified to trade until its registered evaluation passes and is independently reviewed")


def replay_results() -> dict:
    """Recorded replay stages (development, hold-out once run), checked against the current protocol."""
    value = json.loads(RESULTS_PATH.read_text())
    value["protocol_current"] = value["protocol_sha256"] == er.PROTOCOL_SHA256
    return value


def _reading(dev: dict | None, holdout: dict | None, forward_final: dict | None) -> str:
    if forward_final:
        return "FORWARD_" + forward_final["verdict"]
    if holdout:
        verdict = holdout.get("verdict") or ("EXPLORATORY_POSITIVE" if (holdout.get("lower") or 0) > 0
                                             else "EXPLORATORY_NOT_SIGNIFICANT")
        return "HOLDOUT_" + verdict + "_FORWARD_PENDING"
    if dev and dev.get("lower") is not None and dev["lower"] > 0:
        return "DEVELOPMENT_POSITIVE_UNCONFIRMED"
    return "NO_EDGE_IN_DEVELOPMENT_CONFIRMATION_PENDING"


# What the health watchdog reads for each forward tracker (scripts/health_watchdog.py FORWARD_FIELDS).
FAIR_VALUE_KEYS = ("protocol_registered", "completed_session", "expected_decision_session", "freeze_missing",
                   "freeze_window_open", "freeze_deadline", "frozen_months", "missed_months",
                   "unrecorded_missed_months", "next_due_session", "sticky_attention", "decision_code_versions",
                   "evaluation")


def with_unrecorded(conn, fair_value: dict, strategies: dict) -> tuple[dict, dict]:
    """Add the misses no run has recorded: a tracker that stopped running leaves these, and
    they are what the watchdog and the admin card must not mistake for recorded losses."""
    recorded = {c["payload"]["decision_session"][:7] for c in fvf.checks_with_status(conn, ("FREEZE_MISSED", "FREEZE_LATE"))}
    fair_value = {**fair_value, "unrecorded_missed_months": [m for m in fair_value.get("missed_months") or []
                                                             if m[:7] not in recorded]}
    logged = {c["payload"]["decision_session"] for c in sf.checks_with_status(conn, ("FREEZE_MISSED", "FREEZE_LATE"))}
    strategies = {**strategies, "unrecorded_missed_sessions": [s for s in strategies.get("missed_sessions") or []
                                                               if s not in logged]}
    return fair_value, strategies


def _compact_forward(status: dict) -> dict:
    keys = ("forward_id", "protocol_registered", "first_decision_session", "evaluation_months", "completed_session",
            "frozen_sessions", "missed_count", "missed_sessions", "unrecorded_missed_sessions", "freeze_missing",
            "freeze_window_open",
            "sticky_attention", "decision_code_versions", "evaluation")
    latest = status.get("latest_decision") or {}
    return {**{k: status.get(k) for k in keys},
            "latest_decision_session": latest.get("decision_session"), "latest_counts": latest.get("counts"),
            "unresolved_trades": status.get("unresolved_trades")}


def _strategies(results: dict, forward: dict) -> list[dict]:
    final = forward["evaluation"] if forward["evaluation"].get("final") else None
    monitoring = (forward.get("monitoring") or {}).get("strategies") or {}
    out = []
    for key in sf.STRATEGIES:
        dev = (results.get("dev") or {}).get("strategies", {}).get(key)
        holdout = ((results.get("holdout") or {}).get("strategies") or {}).get(key)
        forward_final = (final or {}).get("strategies", {}).get(key)
        out.append({
            "strategy": key, "role": ss.RULES[key]["role"], "definition": ss.DEFINITIONS[key],
            "max_hold_sessions": ss.RULES[key]["max_hold"], "exits": list(ss.RULES[key]["exits"]),
            "reading": _reading(dev, holdout, forward_final),
            "evidence": {"development_2016_2023": dev, "holdout_2024_2026": holdout,
                         "forward": forward_final or {"status": "PENDING", **(monitoring.get(key) or {}),
                                                      "label": "MONITORING_NOT_A_VERDICT"}}})
    return out


def overview(conn, *, now: datetime | None = None) -> dict:
    results = replay_results()
    forward = sf.summary(conn, now=now)
    conn.rollback()
    return fvf.clean({
        "schema_version": "strategy-evidence.v1", "protocol_id": results["protocol_id"],
        "protocol_sha256": results["protocol_sha256"], "protocol_current": results["protocol_current"],
        "units": "reward v5: stock-specific return after costs in units of ex-ante volatility over the maximum "
                 "hold; monthly values are an equal-weight book (sum of rewards / covered stocks)",
        "exits_and_timing": {"exits": ss.DEFINITIONS["exits"], "timing": ss.DEFINITIONS["timing"]},
        "strategies": _strategies(results, forward),
        "development_evidence": (results.get("dev") or {}).get("evidence"),
        "holdout_status": "RECORDED" if results.get("holdout") else "NOT_RUN",
        "forward_shadow": _compact_forward(forward),
        "agent_use": AGENT_USE, "promotion": "NOT_AUTHORIZED", "broker_submission": False,
    })


def for_ticker(conn, ticker: str, *, now: datetime | None = None) -> dict:
    """The latest frozen signals for one stock, its shadow trades, and each strategy's evidence reading."""
    status = sf.summary(conn, now=now, ticker=ticker)   # evaluation and monitoring stay universe-wide
    conn.rollback()
    latest = status.get("latest_decision") or {}
    row = next(iter(latest.get("rows") or []), None)
    flags = (row or {}).get("flags") or ({s: False for s in sf.STRATEGIES} if latest.get("with_bar") else None)
    return fvf.clean({
        "schema_version": "strategy-signals.v1", "ticker": ticker,
        "decision_session": latest.get("decision_session"),
        "frozen_before_next_open": bool(latest),
        "has_bar": latest.get("with_bar") if latest else None,
        "signals": flags,
        "signal_context": {k: row.get(k) for k in ("close", "atr_ratio", "s3")} if row else None,
        "sec_filings_unread": ticker in (latest.get("sec_unavailable") or []),
        "shadow_trades_in_progress": status.get("trades_in_progress"),
        "recent_shadow_trades": status.get("recent_trades"),
        "strategy_readings": {s["strategy"]: s["reading"] for s in _strategies(replay_results(), status)},
        "agent_use": AGENT_USE, "promotion": "NOT_AUTHORIZED", "broker_submission": False,
    })


# ---- learning ------------------------------------------------------------------

def learning_status(conn, *, now: datetime | None = None) -> dict:
    """What the agent has learned so far, from the latest learning-stage receipt and the pooled bandit state."""
    from . import learning
    conn.execute("SET TRANSACTION READ ONLY")
    row = conn.execute("""SELECT details FROM operator_audit
        WHERE event='AGENT_STAGE_FINISHED' AND details->>'stage'='learning'
        ORDER BY id DESC LIMIT 1""").fetchone()
    receipt = row[0] if row and isinstance(row[0], dict) else {}
    pooled = learning.pooled_state(conn)
    arms = learning.current_arms(pooled)
    paper = receipt.get("paper") or {}
    shadow = receipt.get("shadow") or {}
    direction = receipt.get("direction_challenger") or {}
    cumulative = receipt.get("shadow_cumulative") or {}
    observations = {action: int((arm or {}).get("observations") or 0) for action, arm in sorted(arms.items())}
    conn.rollback()
    fair_value = fvf.summary(conn, now=now)
    conn.rollback()
    strategies = sf.summary(conn, now=now)
    conn.rollback()
    fair_value, strategies = with_unrecorded(conn, fair_value, strategies)
    conn.rollback()
    learned = sum(observations.values()) > 0
    return fvf.clean({
        "schema_version": "agent-learning-status.v1",
        "learned_from_outcomes": learned,
        "reading": ("LEARNING_FROM_MATURED_OUTCOMES" if learned else
                    "NOTHING_LEARNED_YET: no decision has reached its outcome horizon under the current contracts"),
        "learning_stage": {k: receipt.get(k) for k in ("key", "status", "started_at", "finished_at", "paper_scored",
                                                       "shadow_scored", "paper_blocked", "shadow_blocked",
                                                       "paper_status", "shadow_status")} if receipt else None,
        "paper_outcomes": {k: paper.get(k) for k in ("latest_completed_session", "horizon_sessions", "scored",
                                                     "pending", "excluded", "blocked")} if paper else None,
        "shadow_outcomes": {"cumulative_outcomes": cumulative.get("outcomes"),
                            "latest_due_session": cumulative.get("latest_due_session"),
                            "scored_this_stage": shadow.get("scored")},
        "bandit": {"state_sequence": (pooled or {}).get("sequence"), "arms_trained": len(arms),
                   "observations_by_action": observations,
                   "rule": "acts only when P(net edge > 0) >= 0.80; otherwise abstains (FLAT)"},
        "direction_test": {k: direction.get(k) for k in ("status", "protocol_id", "decisions", "blocks",
                                                         "required_blocks", "mean_paired_difference")},
        "forward_tests": {
            "fair_value_sector_pe": {k: fair_value.get(k) for k in FAIR_VALUE_KEYS},
            "event_strategies": _compact_forward(strategies),
        },
        "trading_mode": _trading_mode(),
        "promotion": "NOT_AUTHORIZED", "broker_submission": False,
    })


def _trading_mode() -> dict:
    """The owner's real RESEARCH/PAPER switch (agent-lab's policy.mode is a fixed research label)."""
    try:
        from ..agent_trading import get_mode
        mode = get_mode()
        return {k: mode.get(k) for k in ("mode", "version", "updated_at", "broker_submission",
                                         "live_trading_available")}
    except Exception as exc:
        return {"mode": None, "status": "UNAVAILABLE", "reason": type(exc).__name__}
