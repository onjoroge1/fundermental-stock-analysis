import math

import pytest

from stock_machine.valuation_tools import (calculate_dcf,
                                           calculate_expected_return,
                                           calculate_multiple_valuation,
                                           calculate_scenario_values)


def test_dcf_hand_check():
    # 1 year at 0% growth, r=10%, g=2%: FCF=100, TV=100*1.02/0.08=1275
    # PV = (100 + 1275)/1.1 = 1250; equity = 1250 - 250 = 1000; 100 sh -> 10.00
    out = calculate_dcf(fcf_base=100, growth_rates_pct=[0.0],
                        terminal_growth_pct=2.0, discount_rate_pct=10.0,
                        net_debt=250, diluted_shares=100)
    assert math.isclose(out["fair_value_per_share"], 10.0, abs_tol=0.01)
    assert math.isclose(out["enterprise_value"], 1250.0, abs_tol=0.5)


def test_dcf_rejects_terminal_growth_above_discount():
    with pytest.raises(ValueError):
        calculate_dcf(100, [5.0], terminal_growth_pct=12.0,
                      discount_rate_pct=8.0, net_debt=0, diluted_shares=10)


def test_multiple_valuation_per_share():
    out = calculate_multiple_valuation(metric_value=6.5, multiple=20.0)
    assert out["implied_value"] == 130.0


def test_scenarios_probabilities_must_sum_to_one():
    with pytest.raises(ValueError):
        calculate_scenario_values([
            {"name": "bear", "probability": 0.3, "eps": 4.0, "valuation_multiple": 15},
            {"name": "bull", "probability": 0.3, "eps": 6.0, "valuation_multiple": 25},
        ])


def test_scenario_weighted_value():
    out = calculate_scenario_values([
        {"name": "bear", "probability": 0.25, "eps": 4.0, "valuation_multiple": 15},
        {"name": "base", "probability": 0.50, "eps": 5.0, "valuation_multiple": 20},
        {"name": "bull", "probability": 0.25, "eps": 6.0, "valuation_multiple": 25},
    ])
    assert math.isclose(out["probability_weighted_value"],
                        0.25 * 60 + 0.5 * 100 + 0.25 * 150, abs_tol=0.01)


def test_expected_return():
    out = calculate_expected_return(100.0, [
        {"probability": 0.5, "price": 120.0},
        {"probability": 0.5, "price": 90.0},
    ])
    assert math.isclose(out["expected_return_pct"], 5.0, abs_tol=0.01)
    assert math.isclose(out["probability_of_positive_return"], 0.5)
