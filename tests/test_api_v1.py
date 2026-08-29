from stock_machine.api_v1_compat import (
    _prediction_horizon,
    bearish_asymmetry_score,
    bear_strategy_guidance,
)


def test_bearish_asymmetry_rewards_low_bull_ceiling():
    sbux_like = bearish_asymmetry_score(
        expected_return_pct=-39.7,
        bear_downside_pct=-65.1,
        bull_upside_pct=-3.1,
        quality_score=54,
        classification="UNATTRACTIVE",
    )
    tsla_like = bearish_asymmetry_score(
        expected_return_pct=-31.5,
        bear_downside_pct=-74.4,
        bull_upside_pct=59.7,
        quality_score=55,
        classification="UNATTRACTIVE",
    )
    assert sbux_like is not None
    assert tsla_like is not None
    assert sbux_like > tsla_like
    assert 0 <= sbux_like <= 100


def test_bear_put_spread_is_default_for_long_horizon_bear_case():
    result = bear_strategy_guidance(
        expected_return_12m_pct=-25.0,
        expected_return_3m_pct=-7.0,
        bear_downside_pct=-45.0,
        bull_upside_pct=5.0,
        prob_down_20pct=0.45,
    )
    assert result["primary"] == "BEAR_PUT_SPREAD"
    assert result["structure_rules"]["prefer_defined_risk"] is True
    assert any(a["strategy"] == "LONG_PUT" for a in result["alternatives"])


def test_delayed_bear_case_surfaces_calendar_and_diagonal_as_alternatives():
    result = bear_strategy_guidance(
        expected_return_12m_pct=-20.0,
        expected_return_3m_pct=-1.0,
        bear_downside_pct=-35.0,
        bull_upside_pct=10.0,
        prob_down_20pct=0.25,
    )
    names = {a["strategy"] for a in result["alternatives"]}
    assert result["primary"] == "BEAR_PUT_SPREAD"
    assert "PUT_CALENDAR" in names
    assert "PUT_DIAGONAL" in names


def test_non_bearish_forecast_is_watch_not_forced_trade():
    result = bear_strategy_guidance(
        expected_return_12m_pct=3.0,
        expected_return_3m_pct=1.0,
        bear_downside_pct=-20.0,
        bull_upside_pct=30.0,
        prob_down_20pct=0.10,
    )
    assert result["primary"] == "WATCH"


def test_prediction_horizon_prefers_legacy_distribution_with_tail_probabilities():
    prediction = {
        "horizons": {
            "12m": {"prob_positive": 0.41, "prob_down_20pct": 0.37}
        },
        "forecast_distribution": {
            "horizons": [
                {
                    "horizon_days": 252,
                    "probability_up": 0.43,
                    "expected_return": -0.08,
                }
            ]
        },
    }
    row = _prediction_horizon(prediction, "12m")
    assert row["prob_down_20pct"] == 0.37


def test_prediction_horizon_can_read_canonical_list_contract():
    prediction = {
        "forecast_distribution": {
            "horizons": [
                {
                    "horizon_days": 63,
                    "probability_up": 0.48,
                    "expected_return": -0.02,
                },
                {
                    "horizon_days": 252,
                    "probability_up": 0.40,
                    "expected_return": -0.15,
                },
            ]
        }
    }
    row = _prediction_horizon(prediction, "12m")
    assert row["horizon_days"] == 252
    assert row["prob_positive"] == 0.40
