"""The power tooling runs the real evaluators and its statistics are sound."""

from math import sqrt

import pytest

from stock_machine.agent_intelligence import direction, power


def test_panel_is_deterministic_and_shaped_like_production():
    d1, w1 = power.simulate_panel(7, 2, stocks=3)
    d2, _ = power.simulate_panel(7, 2, stocks=3)
    assert d1 == d2 and len(d1) == len(w1) == 3 * 2 * power.HORIZON
    assert {r["incumbent"] for r in d1} <= {"LONG", "SHORT", "FLAT"}
    assert all(r["rewards"]["LONG"] + r["rewards"]["SHORT"] == pytest.approx(-2 * power.COST) for r in d1)


def test_run_once_uses_the_live_evaluators():
    result = power.run_once((3, 12, 0.3, 0.15, 0.3, 200))
    assert set(result) == {"direction_pass", "direction_difference", "direction_detectable",
                           "weights_pass", "weights_difference", "weights_detectable"}
    assert result["direction_detectable"] > 0 and result["weights_detectable"] > 0


def test_newey_west_matches_iid_formula_without_autocorrelation_and_grows_with_it():
    alternating = [1.0, -1.0] * 10
    trending = [1.0] * 10 + [-1.0] * 10
    iid_se = sqrt(sum(v * v for v in alternating) / 20 / 19)
    assert power.newey_west_se(alternating, lag=0) == pytest.approx(iid_se)
    assert power.newey_west_se(trending) > power.newey_west_se(trending, lag=0)
    assert power.newey_west_se(alternating) < power.newey_west_se(alternating, lag=0)


def test_rule_comparison_reports_both_rules():
    row = power.rule_comparison_once((11, 12, 0.8, 200))
    assert row[:2] == (12, 0.8) and all(isinstance(v, bool) for v in row[2:])


def test_detectable_difference_shrinks_with_more_blocks():
    small = direction.detectable([0.1, -0.1, 0.05, -0.05], 12)
    large = direction.detectable([0.1, -0.1, 0.05, -0.05] * 4, 12)
    assert large["detectable_difference_80pct"] < small["detectable_difference_80pct"]
    assert small["detectable_at_minimum_blocks"] < small["detectable_difference_80pct"]
    assert direction.detectable([0.1], 12)["detectable_difference_80pct"] is None
