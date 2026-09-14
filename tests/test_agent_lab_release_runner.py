"""Pure release-proof tests. No production calls or credentials."""
import copy
from uuid import uuid4

import pytest

from scripts.release_agent_lab_0019 import ReleaseError, fingerprint, verify_record, verify_state
from stock_machine.agents.contracts import Policy


def record():
    evidence = {"ticker": "AAPL", "test_fixture": True}
    decision = {"decision_id": str(uuid4()), "ticker": "AAPL", "policy_id": "research-pilot-v1",
        "mode": "RESEARCH", "execution_status": "NOT_ENABLED", "reward": None, "pnl": None,
        "selection_mode": "RULE_BASED", "status": "BLOCKED", "action": "NO_TRADE",
        "rationale": "Explicit test fixture, not an actual decision.", "input_sha256": fingerprint(evidence)}
    initial = {"decision": decision}
    detail = {"decision": decision, "frozen_evidence": evidence, "events": [{"event_type": "RECORDED"}]}
    replay = {"decision": decision, "replayed": True}
    return copy.deepcopy(initial), copy.deepcopy(detail), copy.deepcopy(replay)


def test_blocked_research_is_a_valid_persisted_record_not_a_trade():
    row = verify_record("AAPL", *record())
    assert row["status"] == "BLOCKED" and row["action"] == "NO_TRADE"


@pytest.mark.parametrize("field,value", [("mode", "LIVE"), ("execution_status", "FILLED"),
    ("pnl", 0), ("reward", 0), ("selection_mode", "EXPLORE"), ("ticker", "MSFT"),
    ("rationale", ""), ("status", "APPROVED")])
def test_release_proof_rejects_capability_or_identity_mismatch(field, value):
    initial, detail, replay = record()
    for item in (initial, detail, replay):
        item["decision"][field] = value
    with pytest.raises(ReleaseError):
        verify_record("AAPL", initial, detail, replay)


def test_changed_evidence_or_duplicate_initial_event_rejected():
    initial, detail, replay = record()
    detail["frozen_evidence"]["test_fixture"] = False
    with pytest.raises(ReleaseError, match="HASH_MISMATCH"):
        verify_record("AAPL", initial, detail, replay)
    initial, detail, replay = record()
    detail["events"].append({"event_type": "RECORDED"})
    with pytest.raises(ReleaseError, match="DUPLICATE"):
        verify_record("AAPL", initial, detail, replay)


def test_a_new_decision_on_retry_is_not_a_success():
    initial, detail, replay = record()
    replay["decision"]["decision_id"] = str(uuid4())
    with pytest.raises(ReleaseError, match="IDEMPOTENCY"):
        verify_record("AAPL", initial, detail, replay)


def test_state_requires_every_research_safety_boundary():
    state = {"status": "OK", "policy": Policy().model_dump(mode="json"), "execution": {"status": "NOT_ENABLED"}}
    assert verify_state(state) is state
    for name in ("order_submission", "simulated_execution", "exploration_enabled", "reward_enabled", "qualified_forward_paper"):
        bad = copy.deepcopy(state)
        bad["policy"][name] = True
        with pytest.raises(ReleaseError, match="CAPABILITY"):
            verify_state(bad)
