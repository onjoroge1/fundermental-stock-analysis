from datetime import date

from fastapi.testclient import TestClient

from stock_machine.options.recommendation import (
    choose_expiration_month,
    choose_strike_ladder,
    determine_direction,
)


def test_choose_expiration_month_targets_requested_dte():
    months = [
        {"month": "OCT26", "standard": "20261016"},
        {"month": "DEC26", "standard": "20261218"},
        {"month": "JUN27", "standard": "20270618"},
        {"month": "SEP27", "standard": "20270917"},
    ]
    picked = choose_expiration_month(
        months, 300, today=date(2026, 8, 30), minimum_days=30
    )
    assert picked is not None
    assert picked["month"] == "JUN27"
    assert picked["dte"] > 250


def test_strike_ladder_keeps_spot_and_bear_target_neighborhoods():
    strikes = list(range(40, 121, 5))
    ladder = choose_strike_ladder(strikes, 100.0, anchors=[70.0], limit=12)
    assert len(ladder) <= 12
    assert min(abs(x - 100.0) for x in ladder) <= 5
    assert min(abs(x - 70.0) for x in ladder) <= 5


def test_direction_uses_report_expected_return_edge():
    bearish = {"forecasts": {"twelve_month": {"expected_return_pct": -22.0}}}
    bullish = {"forecasts": {"twelve_month": {"expected_return_pct": 18.0}}}
    neutral = {"forecasts": {"twelve_month": {"expected_return_pct": 2.0}}}
    assert determine_direction(bearish, None, "12m") == "bearish"
    assert determine_direction(bullish, None, "12m") == "bullish"
    assert determine_direction(neutral, None, "12m") == "neutral"


def test_explicit_direction_overrides_auto_signal():
    report = {"forecasts": {"twelve_month": {"expected_return_pct": 20.0}}}
    assert determine_direction(report, None, "12m", "bearish") == "bearish"


def test_recommendation_api_is_one_call(monkeypatch):
    from stock_machine.options import recommendation
    monkeypatch.setattr(
        recommendation,
        "recommend",
        lambda ticker, **kwargs: {
            "status": "OK",
            "ticker": ticker.upper(),
            "direction": kwargs["direction"],
            "horizon": kwargs["horizon"],
            "primary": {"strategy_type": "bear_put_debit_spread"},
            "execution_readiness": {"ready": False},
        },
    )
    from stock_machine.webapp_automation import app
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/api/v1/options/SBUX/recommendation?direction=bearish&horizon=12m&capital=1000"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ticker"] == "SBUX"
    assert payload["primary"]["strategy_type"] == "bear_put_debit_spread"
