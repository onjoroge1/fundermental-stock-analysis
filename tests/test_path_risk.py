from stock_machine.options.path_risk import PathRiskPolicy, assess_mixed_path_risk
from stock_machine.portfolio.expression import ExpressionPolicy
from stock_machine.portfolio.extended_expression import compare_extended


def _calendar(*, right="C", near=100.0, far=100.0, debit=400.0,
              spot=100.0, liquidity=0.9):
    return {
        "status": "OK",
        "strategy_type": ("call_calendar" if right == "C" else "put_calendar")
        if near == far else ("call_diagonal" if right == "C" else "put_diagonal"),
        "valuation_mode": "front_expiry_mark_to_model",
        "symbol": "XYZ",
        "spot_price": spot,
        "near_expiration": "2026-09-18",
        "far_expiration": "2026-10-16",
        "near_leg": {"action": "sell", "right": right, "strike": near,
                     "entry_price": 2.0, "conid": 1},
        "far_leg": {"action": "buy", "right": right, "strike": far,
                    "entry_price": 6.0, "conid": 2,
                    "implied_volatility": 0.30},
        "net_debit": debit,
        "scenario_best_pnl": 300.0,
        "scenario_best_underlying": 105.0 if right == "C" else 95.0,
        "scenario_worst_pnl": -350.0,
        "scenario_worst_underlying": 70.0 if right == "C" else 130.0,
        "liquidity_score": liquidity,
    }


def _position(weight=0.10):
    return {
        "ticker": "XYZ",
        "weight": weight,
        "expected_excess_return_pct": 8.0 if weight > 0 else -8.0,
        "prob_outperform": 0.65 if weight > 0 else 0.35,
        "realized_vol": 0.25,
    }


def test_calendar_economic_bound_is_debit_when_strikes_match():
    result = assess_mixed_path_risk(
        _calendar(),
        position_budget=10000.0,
        event_screen={"status": "CLEAR"},
    )
    assert result["conservative_economic_max_loss"] == 400.0
    assert result["transient_assignment_notional"] == 10000.0
    assert result["automation_eligible"] is True


def test_call_diagonal_adds_adverse_far_minus_near_strike_gap():
    result = assess_mixed_path_risk(
        _calendar(near=100.0, far=105.0, debit=300.0),
        position_budget=10000.0,
        event_screen={"status": "CLEAR"},
    )
    assert result["conservative_economic_max_loss"] == 800.0


def test_put_diagonal_adds_adverse_near_minus_far_strike_gap():
    result = assess_mixed_path_risk(
        _calendar(right="P", near=105.0, far=100.0, debit=300.0),
        position_budget=10000.0,
        event_screen={"status": "CLEAR"},
    )
    assert result["conservative_economic_max_loss"] == 800.0
    assert result["transient_assignment_notional"] == 10500.0


def test_unknown_event_screen_fails_closed():
    result = assess_mixed_path_risk(_calendar(), position_budget=10000.0)
    assert result["automation_eligible"] is False
    assert "clear event/assignment screen required for automation" in result["reasons"]


def test_assignment_notional_can_block_small_budget_even_when_debit_is_small():
    result = assess_mixed_path_risk(
        _calendar(debit=200.0),
        position_budget=3000.0,
        event_screen={"status": "CLEAR"},
        policy=PathRiskPolicy(max_assignment_notional_multiple_of_budget=2.5),
    )
    assert result["conservative_economic_max_loss"] == 200.0
    assert result["automation_eligible"] is False
    assert any("assignment notional" in reason for reason in result["reasons"])


def test_clear_calendar_can_enter_extended_expression_comparison():
    result = compare_extended(
        _position(0.10),
        [_calendar()],
        ExpressionPolicy(portfolio_value=100000.0, option_improvement_margin=0.0),
        event_screens={"call_calendar": {"status": "CLEAR"}},
    )
    assert result["expression"] in {"stock", "option_overlay"}
    if result["expression"] == "option_overlay":
        assert result["selected"]["strategy_type"] == "call_calendar"
        assert result["selected"]["path_risk"]["automation_eligible"] is True


def test_mixed_direction_must_match_portfolio_target():
    result = compare_extended(
        _position(-0.10),
        [_calendar(right="C")],
        ExpressionPolicy(portfolio_value=100000.0, option_improvement_margin=0.0),
        event_screens={"call_calendar": {"status": "CLEAR"}},
    )
    assert result["expression"] == "stock"
    assert any("direction" in reason for row in result["rejected"] for reason in row["reasons"])
