from stock_machine.portfolio.expression import ExpressionPolicy
from stock_machine.portfolio.extended_expression import compare_extended


def _position():
    return {
        "ticker": "XYZ",
        "weight": 0.05,
        "expected_excess_return_pct": 8.0,
        "prob_outperform": 0.62,
        "realized_vol": 0.25,
    }


def _candidate():
    return {
        "status": "OK",
        "strategy_type": "call_calendar",
        "valuation_mode": "front_expiry_mark_to_model",
        "spot_price": 100.0,
        "near_expiration": "2026-10-16",
        "far_expiration": "2027-01-15",
        "near_leg": {"strike": 100.0, "right": "C"},
        "far_leg": {"strike": 100.0, "right": "C"},
        "net_debit": 200.0,
        "liquidity_score": 1.0,
        "scenario_best_pnl": 800.0,
        "scenario_best_underlying": 110.0,
        "scenario_worst_pnl": -200.0,
    }


def test_candidate_specific_clear_event_screen_allows_mixed_comparison():
    seen = []

    def factory(candidate):
        seen.append((candidate["near_expiration"], candidate["far_expiration"]))
        return {"status": "CLEAR", "reasons": [], "warnings": []}

    result = compare_extended(
        _position(), [_candidate()],
        ExpressionPolicy(portfolio_value=100_000.0, option_improvement_margin=0.0),
        event_screen_factory=factory,
    )
    assert seen == [("2026-10-16", "2027-01-15")]
    assert result["expression"] == "option_overlay"
    assert result["selected"]["event_screen"]["status"] == "CLEAR"
    assert result["selected"]["path_risk"]["automation_eligible"] is True


def test_candidate_specific_block_keeps_calendar_analysis_only():
    result = compare_extended(
        _position(), [_candidate()],
        ExpressionPolicy(portfolio_value=100_000.0, option_improvement_margin=0.0),
        event_screen_factory=lambda candidate: {
            "status": "BLOCK",
            "reasons": ["earnings event occurs before front expiry"],
            "warnings": [],
        },
    )
    assert result["expression"] == "stock"
    assert result["analysis_only"][0]["event_screen"]["status"] == "BLOCK"
    assert result["analysis_only"][0]["path_risk"]["automation_eligible"] is False
