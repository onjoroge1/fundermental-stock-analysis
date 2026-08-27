"""Scanner tests: filters, ranking, and the no-upside-risk detector."""
from __future__ import annotations

import pytest

from stock_machine.options.scanner import ScanPolicy, scan
from stock_machine.options.simulator import StrategyBuildError
from tests.test_options_simulator import _chain, _opt


def _wide_chain():
    """Chain with enough strikes to make combinations meaningful."""
    chain = _chain()
    for strike, cb, ca, pb, pa in (
        (80.0, 21.0, 21.4, 1.0, 1.2),
        (120.0, 1.0, 1.2, 21.0, 21.4),
    ):
        chain.options.append(_opt(strike, "C", cb, ca))
        chain.options.append(_opt(strike, "P", pb, pa))
    return chain


def _rich_chain():
    """Elevated premiums so at least one jade lizard collects a credit larger
    than its call width — the defining no-upside-risk condition."""
    chain = _wide_chain()
    for option in chain.options:
        if option.contract.right == "P" and option.contract.strike <= 100.0:
            option.quote.bid += 6.0
            option.quote.ask += 6.0
    return chain


def test_scan_states_its_objective_and_counts():
    r = scan(_wide_chain(), "jade_lizard", ScanPolicy(top_n=3))
    assert "return on risk" in r["objective"]
    assert r["combinations_evaluated"] > 0
    assert len(r["results"]) <= 3
    assert r["disclaimer"]


def test_no_upside_risk_filter_only_keeps_qualifying_structures():
    r = scan(_rich_chain(), "jade_lizard",
             ScanPolicy(require_no_upside_risk=True, top_n=20))
    assert r["results"], "expected at least one qualifying combination"
    assert all(row["no_upside_risk"] for row in r["results"])
    # every kept structure must be non-negative far above the top strike
    for row in r["results"]:
        assert row["net_credit"] >= (row["strikes"][2] - row["strikes"][1]) * 100 - 1e-6


def test_rejections_are_reported_not_hidden():
    r = scan(_wide_chain(), "jade_lizard",
             ScanPolicy(require_no_upside_risk=True, top_n=50))
    assert r["rejected_reasons"], "filtered-out combinations must be explained"
    assert sum(r["rejected_reasons"].values()) + r["candidates_passing"] == \
        r["combinations_evaluated"]


def test_results_are_sorted_by_the_stated_objective():
    r = scan(_wide_chain(), "bull_put_credit_spread", ScanPolicy(top_n=10))
    rors = [x["return_on_risk"] for x in r["results"] if x["return_on_risk"] is not None]
    assert rors == sorted(rors, reverse=True)


def test_min_credit_filter_applies():
    loose = scan(_wide_chain(), "bull_put_credit_spread", ScanPolicy(top_n=50))
    strict = scan(_wide_chain(), "bull_put_credit_spread",
                  ScanPolicy(min_credit=500.0, top_n=50))
    assert strict["candidates_passing"] < loose["candidates_passing"]
    assert all(x["net_credit"] >= 500.0 for x in strict["results"])


def test_expected_value_requires_a_forecast():
    with pytest.raises(StrategyBuildError, match="prediction-lab forecast"):
        scan(_wide_chain(), "jade_lizard", ScanPolicy(objective="expected_value"))


def test_expected_value_uses_the_forecast_distribution():
    forecast = {
        "status": "OK", "primary_model": "bootstrap",
        "models": {"bootstrap": {"horizons": {"1m": {
            "p10": 80.0, "p25": 90.0, "p50": 100.0, "p75": 110.0, "p90": 120.0}}}},
    }
    r = scan(_wide_chain(), "jade_lizard",
             ScanPolicy(objective="expected_value", top_n=5), forecast=forecast)
    assert "UNCALIBRATED" in r["objective"]
    evs = [x["expected_pnl"] for x in r["results"]]
    assert all(e is not None for e in evs)
    assert evs == sorted(evs, reverse=True)


def test_unknown_strategy_rejected():
    with pytest.raises(StrategyBuildError):
        scan(_wide_chain(), "nope", ScanPolicy())
