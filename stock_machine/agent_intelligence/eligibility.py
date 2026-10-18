"""Which decisions the learner may learn from, separate from paper eligibility.

Paper trading requires verified financials. Learning does not need them: a
decision's counterfactual outcome is a price fact. Most covered stocks are
blocked only because their total debt has not been reconciled to a filing
(financial_integrity), which made the learner see about 5 of 54 stocks. Those
decisions may train the model with the fundamental signal withheld, so it is
seen as missing (never as an unverified number). Every other blocker (stale
or missing inputs, thesis invalidation, negative headlines) still excludes
the decision. Paper eligibility is never changed here.
"""

from __future__ import annotations

PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
FUNDAMENTALS_WITHHELD = "FUNDAMENTALS_WITHHELD"

# State blockers that only say the financial data is unverified.
FUNDAMENTAL_STATE_BLOCKERS = frozenset({
    "DATA_QUALITY_NOT_PASS",
    "FINANCIAL_INTEGRITY_NOT_VERIFIED",
    "AGENT_DECISION_NOT_RECORDED",  # allowed only when the journal's reasons are fundamental
})
# Journal (research contract) blockers with the same meaning.
FUNDAMENTAL_DECISION_BLOCKERS = frozenset({
    "FINANCIAL_DEPENDENCIES_UNVERIFIED",
    "REQUIRED_DATA_NOT_VERIFIED",
    "DATA_QUALITY_NOT_PASS",
})


def learning_eligibility(state: dict, decision: dict) -> str | None:
    """PAPER_ELIGIBLE, FUNDAMENTALS_WITHHELD, or None (not a training sample)."""
    if state.get("paper_eligible") is True:
        return PAPER_ELIGIBLE
    state_blockers = set(state.get("blockers") or [])
    decision_blockers = set(decision.get("blockers") or [])
    if not state_blockers or not state_blockers <= FUNDAMENTAL_STATE_BLOCKERS:
        return None
    if decision.get("status") != "RECORDED" and not decision_blockers:
        return None  # blocked for an unstated reason
    if not decision_blockers <= FUNDAMENTAL_DECISION_BLOCKERS:
        return None
    features = (state.get("technical") or {}).get("features") or {}
    if features.get("realized_vol_20") is None:
        return None  # price-based inputs must be present
    return FUNDAMENTALS_WITHHELD


def withhold_fundamentals(state: dict) -> dict:
    components = dict(state.get("signal_components") or {})
    components["fundamental"] = None
    return {
        **state,
        "signal_components": components,
        "learning_eligibility": FUNDAMENTALS_WITHHELD,
        "fundamental_withheld_reason": "FINANCIAL_DATA_UNVERIFIED",
    }


def is_learning_eligible(state: dict) -> bool:
    return state.get("paper_eligible") is True or (
        state.get("learning_eligibility") == FUNDAMENTALS_WITHHELD
        and (state.get("signal_components") or {}).get("fundamental") is None
    )


def basis(state: dict) -> str:
    return PAPER_ELIGIBLE if state.get("paper_eligible") is True else FUNDAMENTALS_WITHHELD
