from stock_machine.portfolio.expression import ExpressionPolicy
from stock_machine.portfolio.extended_expression import compare_extended


def _position(weight=0.05):
    return {
        "ticker": "XYZ",
        "weight": weight,
        "expected_excess_return_pct": 8.0,
        "prob_outperform": 0.62,
        "realized_vol": 0.25,
    }


def test_calendar_remains_analysis_only_without_exact_risk_bound():
    candidate = {
        "status": "OK",
        "strategy_type": "call_calendar",
        "valuation_mode": "front_expiry_mark_to_model",
        "scenario_best_pnl": 250.0,
        "scenario_worst_pnl": -180.0,
    }
    result = compare_extended(_position(), [candidate])
    assert result["expression"] == "stock"
    assert result["analysis_only"][0]["strategy_type"] == "call_calendar"


def test_covered_call_rejected_for_short_target():
    candidate = {
        "status": "OK", "strategy_type": "covered_call",
        "max_loss": 3000.0, "liquidity_score": 0.9,
        "spot_price": 100.0, "short_option": {"strike": 110.0},
        "net_option_credit": 200.0,
    }
    result = compare_extended(_position(-0.05), [candidate])
    assert result["expression"] == "stock"
    assert "long stock target" in result["rejected"][0]["reasons"][0]


def test_covered_call_can_win_when_exact_risk_and_score_clear_gates():
    candidate = {
        "status": "OK", "strategy_type": "covered_call",
        "front_expiration": "2026-09-18",
        "max_profit": 1200.0, "max_loss": 3000.0, "breakeven": 97.0,
        "liquidity_score": 1.0,
        "spot_price": 100.0, "short_option": {"strike": 115.0},
        "net_option_credit": 500.0,
    }
    policy = ExpressionPolicy(portfolio_value=100000.0, option_improvement_margin=0.0)
    result = compare_extended(_position(0.05), [candidate], policy)
    assert result["expression"] in {"stock", "option_overlay"}
    if result["expression"] == "option_overlay":
        assert result["selected"]["strategy_type"] == "covered_call"


def test_covered_call_respects_position_capital_budget():
    candidate = {
        "status": "OK", "strategy_type": "covered_call",
        "max_loss": 9000.0, "liquidity_score": 0.9,
        "spot_price": 100.0, "short_option": {"strike": 110.0},
        "net_option_credit": 250.0,
    }
    result = compare_extended(_position(0.05), [candidate])
    assert result["expression"] == "stock"
    assert "capital budget" in result["rejected"][0]["reasons"][0]
