"""Learning may use decisions blocked only by unverified financials, fundamentals withheld."""

import pytest

from stock_machine.agent_intelligence import bandit, eligibility, outcomes

FUNDAMENTAL_STATE = ["AGENT_DECISION_NOT_RECORDED", "DATA_QUALITY_NOT_PASS", "FINANCIAL_INTEGRITY_NOT_VERIFIED"]
FUNDAMENTAL_JOURNAL = ["FINANCIAL_DEPENDENCIES_UNVERIFIED", "REQUIRED_DATA_NOT_VERIFIED", "DATA_QUALITY_NOT_PASS"]


def state(**overrides):
    return {"paper_eligible": False, "blockers": list(FUNDAMENTAL_STATE),
            "signal_components": {"fundamental": 0.7, "technical": 0.2, "news": 0.0, "regime": 0.1},
            "technical": {"features": {"realized_vol_20": 0.3}}, **overrides}


def decision(**overrides):
    return {"status": "BLOCKED", "blockers": list(FUNDAMENTAL_JOURNAL), **overrides}


def test_unverified_financials_alone_still_train_the_model():
    assert eligibility.learning_eligibility(state(), decision()) == eligibility.FUNDAMENTALS_WITHHELD
    withheld = eligibility.withhold_fundamentals(state())
    assert withheld["paper_eligible"] is False                 # paper stays blocked
    assert withheld["signal_components"]["fundamental"] is None
    x = dict(zip(bandit.FEATURE_NAMES, bandit.context_vector(withheld)))
    assert x["fundamental"] == 0.0 and x["fundamental_missing"] == 1.0
    assert eligibility.is_learning_eligible(withheld)
    assert eligibility.basis(withheld) == eligibility.FUNDAMENTALS_WITHHELD


@pytest.mark.parametrize("s,d", [
    (state(), decision(blockers=FUNDAMENTAL_JOURNAL + ["THESIS_INVALIDATION_REQUIRES_REVIEW"])),
    (state(), decision(blockers=FUNDAMENTAL_JOURNAL + ["STALE_INPUTS"])),
    (state(), decision(blockers=["CRITICAL_INPUTS_MISSING"])),
    (state(blockers=FUNDAMENTAL_STATE + ["HIGH_MATERIALITY_NEGATIVE_HEADLINE_CONTEXT"]), decision()),
    (state(technical={"features": {}}), decision()),
    (state(), decision(blockers=[])),          # blocked for an unstated reason
    (state(blockers=[]), decision()),          # ineligible without any stated blocker
])
def test_any_other_blocker_still_excludes_the_decision(s, d):
    assert eligibility.learning_eligibility(s, d) is None


def test_paper_eligible_decisions_are_unchanged():
    ok = state(paper_eligible=True, blockers=[])
    assert eligibility.learning_eligibility(ok, decision(status="RECORDED", blockers=[])) == eligibility.PAPER_ELIGIBLE
    assert eligibility.is_learning_eligible(ok)


def test_outcome_scanner_accepts_withheld_but_not_unflagged_blocked_runs():
    window = {"contract": outcomes.CONTRACT, "execution_session": "2026-09-01", "due_session": "2026-09-30"}
    run = {"learning_contract": outcomes.CONTRACT, "learning": window,
           "state": eligibility.withhold_fundamentals(state())}
    assert outcomes.learning_status(run, "2026-10-01") == ("READY_COUNTERFACTUAL", "2026-09-30")
    blocked = {**run, "state": state()}
    assert outcomes.learning_status(blocked, "2026-10-01")[0] == "EXCLUDED_BLOCKED_STATE"
    # A forged flag without the fundamental actually withheld is not accepted.
    forged = {**run, "state": {**state(), "learning_eligibility": eligibility.FUNDAMENTALS_WITHHELD}}
    assert outcomes.learning_status(forged, "2026-10-01")[0] == "EXCLUDED_BLOCKED_STATE"
