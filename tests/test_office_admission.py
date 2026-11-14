"""Checks on the strategy inventory and the admission rule that need no database."""
import copy
import inspect
import json

import pytest

from stock_machine.office import admission, inventory, roles, tools

ROWS = inventory.read()
BY_ID = {r["strategy_id"]: r for r in ROWS}
CANDIDATES = ["X2-FLOW", "X2-STRESS", "QM-1", "fair-value-convergence.v1:sector_timed_120"]
BOOK = {"book_id": "experimental", "max_active_deployments": 2, "limits": {"borrow_policy": "FIXTURE"}}


def as_stored(row):
    return {**{c: row[c] for c in inventory.COLUMNS}, "record": {k: v for k, v in row.items() if k not in inventory.COLUMNS}}


def run(row, **kw):
    args = dict(book=BOOK, active=[], excluded_hashes=set(), signoff=True, paper_enabled=True,
                supported=frozenset({"NATIVE", "EXTERNAL_LOCAL"}))
    args.update(kw)
    return admission.evaluate(as_stored(row), **args)


def fails(checks):
    return {c["check"]: c["codes"] for c in checks if c["outcome"] == "FAIL"}


def test_the_inventory_tells_candidates_from_excluded_strategies_and_keeps_their_own_labels():
    assert [r["strategy_id"] for r in ROWS if r["admissible"]] == CANDIDATES and len(ROWS) == 28
    assert all(r["evidence_tier"] == "DEVELOPMENT" for r in ROWS if r["admissible"])    # none is a tested edge
    assert BY_ID["EXT-FADE"]["original_status"] == "Closed, FAIL"
    assert BY_ID["EXT-FADE-X2"]["original_status"] == "Parked"
    assert BY_ID["GAPML-2"]["original_status"].startswith("Closed, FAIL")
    assert BY_ID["event-strategy-replay.v1:S2"]["original_status"].startswith("Forward shadow, pending")
    assert BY_ID["technical-setups"]["evidence_tier"] == "NONE"                         # missing evidence
    for row in ROWS:
        if not row["admissible"]:
            assert row["execution_kind"] == "NONE" and row["exclusion_reason"] and row["development"] is None
    assert not any(word in json.dumps(ROWS).lower() for word in ("qualified to trade", "recommend buying"))


def test_the_inventory_points_at_the_frozen_files_and_holds_no_market_data():
    from tests.test_fair_value_forward import PINNED_PROTOCOL_SHA256
    fair_value = BY_ID["fair-value-convergence.v1:sector_timed_120"]
    assert fair_value["hashes"] == {"fair_value_forward.PROTOCOL_SHA256": PINNED_PROTOCOL_SHA256}
    assert BY_ID["X2-FLOW"]["hashes"]["X2-FLOW_SPEC_v1.md"].startswith("9ebd450a")
    assert BY_ID["X2-FLOW"]["risk_group"] == BY_ID["X2-STRESS"]["risk_group"]           # close to one bet
    assert {r["runner_status"] for r in ROWS if r["source_repository"] == "research" and r["admissible"]} == {
        "BLOCKED_LICENSED_DATA"}
    text = inventory.PATH.read_text()
    assert len(text) < 40_000 and not any(key in text for key in ('"bars"', '"quotes"', '"ohlc', '"close"'))


@pytest.mark.parametrize("strategy_id", CANDIDATES)
def test_no_real_candidate_is_admitted_today(strategy_id):
    excluded = {h for r in ROWS if not r["admissible"] for h in r["hashes"].values()}
    checks = admission.evaluate(as_stored(BY_ID[strategy_id]), book=None, active=[], excluded_hashes=excluded,
                                signoff=False, paper_enabled=False)
    assert admission.outcome(checks) == "BLOCKED"
    failed = fails(checks)
    assert set(failed) == {3, 5, 6} and "EXECUTION_NOT_SUPPORTED" in failed[3]
    assert failed[5] == ["NO_REVIEWER_SIGNOFF"] and failed[6] == ["NO_BOOK"]
    # weak evidence stays labelled weak; it is never rounded up
    by_check = {c["check"]: c for c in checks}
    assert by_check[1]["outcome"] == "PASS_WEAK" and by_check[1]["codes"] == ["DEVELOPMENT_ONLY"]
    assert by_check[2]["outcome"] == "PASS_WEAK" and by_check[4]["outcome"] == "PASS"


def test_nothing_is_supported_for_execution_in_this_change():
    from stock_machine.office import paper
    assert admission.SUPPORTED_EXECUTION == frozenset() and paper.RULES == {}    # no real rule is registered
    assert fails(run(BY_ID["QM-1"], supported=None))[3] == ["EXECUTION_NOT_SUPPORTED"]


def test_a_candidate_with_every_check_met_is_admitted_as_weak_not_as_proven():
    checks = run(BY_ID["QM-1"])
    assert admission.outcome(checks) == "ADMITTED" and fails(checks) == {}
    assert [c["outcome"] for c in checks] == ["PASS", "PASS_WEAK", "PASS_WEAK", "PASS", "PASS", "PASS", "PASS"]


@pytest.mark.parametrize("strategy_id", ["EXT-FADE", "EXT-FADE-X2", "GU-L", "GAPML-1", "event-strategy-replay.v1:S1"])
def test_a_closed_failed_or_parked_strategy_is_never_admitted(strategy_id):
    checks = run(BY_ID[strategy_id])
    assert admission.outcome(checks) == "BLOCKED"
    assert fails(checks)[0] == ["ORIGINAL_STATUS_NOT_ADMISSIBLE"] and 1 in fails(checks)


def test_the_rules_of_an_excluded_strategy_cannot_come_back_under_a_new_name():
    relabelled = copy.deepcopy(BY_ID["QM-1"])
    relabelled.update(strategy_id="MOMENTUM-QUALITY-NEW", risk_group="NEW")
    closed_hash = "ab" * 32
    relabelled["hashes"] = {"rules.md": closed_hash}
    assert admission.outcome(run(relabelled)) == "ADMITTED"
    checks = run(relabelled, excluded_hashes={closed_hash})
    assert admission.outcome(checks) == "BLOCKED" and fails(checks) == {0: ["SAME_RULES_AS_EXCLUDED_STRATEGY"]}


def changed(strategy_id="QM-1", **record):
    row = copy.deepcopy(BY_ID[strategy_id])
    for key, value in record.items():
        if isinstance(value, dict) and isinstance(row.get(key), dict):
            row[key].update(value)
        else:
            row[key] = value
    return row


@pytest.mark.parametrize("row, kw, check, code", [
    (changed(development=None), {}, 1, "NO_DEVELOPMENT_RESULT"),
    (changed(development={"net_positive": False}), {}, 1, "NOT_NET_POSITIVE"),
    (changed(development={"reproducible": None}), {}, 1, "NOT_REPRODUCIBLE"),
    (changed(defects=[{"category": "LOOK_AHEAD", "text": "t", "resolved": False}]), {}, 2, "UNRESOLVED_LOOK_AHEAD"),
    (changed(defects=[{"category": "TIMING", "text": "t", "resolved": False}]), {}, 2, "UNRESOLVED_TIMING"),
    (changed(hashes=None), {}, 3, "FROZEN_HASHES_MISSING"),
    (changed(development={"cost_model": ""}), {}, 3, "COST_MODEL_MISSING"),
    (changed(), {"paper_enabled": False}, 3, "PAPER_BOOK_SWITCHED_OFF"),
    (changed("X2-FLOW"), {"book": {**BOOK, "limits": {}}}, 3, "SHORTS_NEED_A_BORROW_POLICY"),
    (changed(forward_protocol={"stop_criteria": ""}), {}, 4, "FORWARD_PROTOCOL_INCOMPLETE"),
    (changed(forward_protocol=None), {}, 4, "FORWARD_PROTOCOL_INCOMPLETE"),
    (changed(), {"signoff": False}, 5, "NO_REVIEWER_SIGNOFF"),
    (changed(), {"book": None}, 6, "NO_BOOK"),
    (changed(), {"active": [{"strategy_id": "QM-1", "risk_group": "OLD"}]}, 6, "STRATEGY_ALREADY_ACTIVE"),
    (changed("X2-STRESS"), {"active": [{"strategy_id": "X2-FLOW", "risk_group": "X2-FADE"}]}, 6,
     "RISK_GROUP_ALREADY_ACTIVE"),
    (changed(), {"active": [{"strategy_id": "A", "risk_group": "A"}, {"strategy_id": "B", "risk_group": "B"}]}, 6,
     "BOOK_FULL"),
])
def test_each_missing_requirement_blocks_with_its_own_code(row, kw, check, code):
    checks = run(row, **kw)
    assert admission.outcome(checks) == "BLOCKED" and code in fails(checks)[check]


def test_a_resolved_critical_defect_no_longer_blocks_and_open_minor_ones_are_shown():
    checks = run(changed(defects=[{"category": "TIMING", "text": "t", "resolved": True},
                                  {"category": "COST", "text": "t", "resolved": False}]))
    assert admission.outcome(checks) == "ADMITTED" and checks[2] == {
        "check": 2, "name": admission.NAMES[2], "outcome": "PASS_WEAK", "codes": ["OPEN_COST"]}


@pytest.mark.parametrize("bad, code", [
    ({"hashes": {"a": "short"}}, "INVENTORY_HASH_INVALID"),
    ({"admissible": True, "execution_kind": "NONE"}, "INVENTORY_EXECUTION_KIND_INVALID"),
    ({"admissible": False, "execution_kind": "NATIVE", "exclusion_reason": "x"}, "INVENTORY_EXECUTION_KIND_INVALID"),
    ({"admissible": False, "execution_kind": "NONE", "exclusion_reason": ""}, "INVENTORY_EXCLUSION_REASON_MISSING"),
    ({"admissible": "yes"}, "INVENTORY_RECORD_INVALID"),
])
def test_a_malformed_inventory_record_is_refused(bad, code):
    with pytest.raises(ValueError, match=code):
        inventory.validate({**copy.deepcopy(BY_ID["QM-1"]), **bad})
    incomplete = copy.deepcopy(BY_ID["QM-1"])
    del incomplete["forward_protocol"]
    with pytest.raises(ValueError, match="INVENTORY_RECORD_INCOMPLETE"):
        inventory.validate(incomplete)


def test_only_the_portfolio_manager_can_decide_and_only_experimental_paper_can_be_created():
    decision = dict(strategy_id="QM-1", version="v", book_id=None, policy_version="p", outcome="ADMITTED",
                    checks=[], input_sha256="0" * 64, reviewer_run_id=None)
    for role in ("analyst", "reviewer", "qa", "scout", "scanner", "controller"):
        with pytest.raises(roles.ToolNotAllowed, match="TOOL_NOT_ALLOWED_FOR_ROLE"):
            tools.write_admission_decision(role, None, **decision)
        with pytest.raises(roles.ToolNotAllowed, match="TOOL_NOT_ALLOWED_FOR_ROLE"):
            tools.write_deployment_state(role, None, strategy={}, book_id="b", state="EXPERIMENTAL_PAPER",
                                         decision_id="d")
    for state in ("QUALIFIED_PAPER", "LIVE", "PAUSED", "RETIRED", "BLOCKED", ""):
        with pytest.raises(roles.ToolNotAllowed, match="DEPLOYMENT_STATE_NOT_ALLOWED"):
            tools.write_deployment_state("portfolio_manager", None, strategy={}, book_id="b", state=state,
                                         decision_id="d")
    source = inspect.getsource(admission) + inspect.getsource(inventory)
    assert "QUALIFIED_PAPER" not in source and "'LIVE'" not in source and '"LIVE"' not in source
    assert "providers" not in source                                   # no model takes part in admission
