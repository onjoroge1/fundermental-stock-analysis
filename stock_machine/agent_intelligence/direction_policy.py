"""Owner-gated switch from the heuristic direction to the learned one.

The learned direction (direction.challenger) acts on paper only when all hold:
1. the owner explicitly approved LEARNED (append-only audit event);
2. the approval was made while the pre-registered test (current protocol)
   reported PASS_REQUIRES_INDEPENDENT_REVIEW;
3. the latest learning-stage evaluation still reports that pass under the
   same protocol hash.

If later evidence stops passing, or the protocol changes, decisions revert to
the heuristic by themselves; the owner can also revert at any time. Nothing
here touches broker execution. The counterfactual labels the test uses do not
depend on which direction acts, so the test stays valid after a switch.
"""

from __future__ import annotations

from . import direction

POLICIES = ("HEURISTIC", "LEARNED")
EVENT = "DIRECTION_POLICY_SET"
PASS = "PASS_REQUIRES_INDEPENDENT_REVIEW"
TO_STATE = {"LONG": "BULLISH", "SHORT": "BEARISH", "FLAT": "NEUTRAL"}


def setting(conn) -> dict:
    row = conn.execute(
        "SELECT details FROM operator_audit WHERE event=%s ORDER BY id DESC LIMIT 1", (EVENT,)
    ).fetchone()
    return row[0] if row else {"policy": "HEURISTIC", "version": 0}


def latest_evaluation(conn) -> dict | None:
    """The direction test result saved by the most recent learning-stage run."""
    row = conn.execute(
        """SELECT details->'direction_challenger' FROM operator_audit
        WHERE event='AGENT_STAGE_FINISHED' AND details->>'key' LIKE 'agent-stage:learning:%%'
          AND jsonb_typeof(details->'direction_challenger')='object'
        ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    return row[0] if row else None


def _passing(evaluation: dict | None) -> bool:
    return bool(
        evaluation
        and evaluation.get("status") == PASS
        and evaluation.get("protocol_sha256") == direction.PROTOCOL_SHA256
    )


def effective(conn) -> dict:
    """Which direction drives paper decisions right now, and why."""
    current, evaluation = setting(conn), latest_evaluation(conn)
    base = {
        "setting": current.get("policy"),
        "setting_version": current.get("version", 0),
        "protocol_sha256": direction.PROTOCOL_SHA256,
        "evaluation_status": (evaluation or {}).get("status"),
    }
    if current.get("policy") != "LEARNED":
        return {**base, "policy": "HEURISTIC", "reason": "NOT_APPROVED"}
    if current.get("protocol_sha256") != direction.PROTOCOL_SHA256:
        return {**base, "policy": "HEURISTIC", "reason": "PROTOCOL_CHANGED_SINCE_APPROVAL"}
    if not _passing(evaluation):
        return {**base, "policy": "HEURISTIC", "reason": "EVIDENCE_NO_LONGER_PASSES"}
    return {**base, "policy": "LEARNED", "reason": "APPROVED_AND_PASSING"}


def set_policy(conn, actor: str, policy: str, expected_version: int, reason: str) -> dict:
    """Owner action; refuses LEARNED unless the test currently passes."""
    from ..admin_panel.store import audit

    if policy not in POLICIES:
        raise ValueError("DIRECTION_POLICY_INVALID")
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('direction-policy',0))")
    current = setting(conn)
    if int(current.get("version", 0)) != expected_version:
        raise ValueError("DIRECTION_POLICY_CHANGED_RELOAD")
    evaluation = latest_evaluation(conn)
    if policy == "LEARNED" and not _passing(evaluation):
        raise ValueError("DIRECTION_TEST_NOT_PASSED")
    details = {
        "policy": policy,
        "version": expected_version + 1,
        "before": current.get("policy"),
        "protocol_sha256": direction.PROTOCOL_SHA256,
        "evaluation_at_approval": {
            k: (evaluation or {}).get(k)
            for k in ("status", "blocks", "decisions", "mean_paired_difference",
                      "lower_95pct_paired_difference", "detectable_difference_80pct")
        },
        "reason": reason[:300],
        "broker_submission": False,
    }
    audit(conn, actor, EVENT, details)
    return {**details, "effective": effective(conn)}


def apply(state: dict, frozen_challenger: dict, policy: dict) -> dict:
    """Return the state the router should see; never changes the frozen test inputs."""
    if policy.get("policy") != "LEARNED" or state.get("paper_eligible") is not True:
        return {**state, "direction_source": "HEURISTIC"}
    learned = TO_STATE[frozen_challenger["challenger"]]
    return {
        **state,
        "heuristic_direction": state.get("direction"),
        "direction": learned,
        "direction_source": "LEARNED",
    }
