import pytest
from pydantic import ValidationError

from stock_machine.forecasts import from_prediction_lab, from_stockpredictor
from stock_machine.forecasts.adapters import ForecastAdapterError
from stock_machine.forecasts.models import PriceQuantiles
from stock_machine.prediction import _attach_canonical_contract


def prediction_lab_payload() -> dict:
    return {
        "status": "OK",
        "ticker": "SPY",
        "as_of": "2026-08-17",
        "last_price": 650.0,
        "primary_model": "bootstrap",
        "models": {
            "bootstrap": {
                "horizons": {
                    "1m": {
                        "days": 21,
                        "p10": 610.0,
                        "p25": 630.0,
                        "p50": 655.0,
                        "p75": 670.0,
                        "p90": 690.0,
                        "prob_positive": 0.54,
                    }
                }
            }
        },
        "validation": {
            "verdict": {
                "primary_model": "bootstrap",
                "lstm_beats_baseline": False,
            }
        },
        "methodology": {"limitations": "price-history-only"},
    }


def stockpredictor_payload() -> dict:
    return {
        "symbol": "AAPL",
        "as_of": "2026-08-17",
        "current_price": 250.0,
        "calibration_status": "calibrated",
        "baseline_status": "beats_baseline",
        "predictions": {
            "h10": {
                "probability_up": 0.58,
                "probability_down": 0.42,
                "expected_return": 0.02,
                "predicted_price": 255.0,
                "confidence": 0.71,
                "ret_p10": -0.05,
                "ret_p50": 0.02,
                "ret_p90": 0.09,
                "band_p10": 237.5,
                "band_p50": 255.0,
                "band_p90": 272.5,
            }
        },
    }


def test_prediction_lab_adapter_preserves_baseline_and_uncertainty():
    result = from_prediction_lab(prediction_lab_payload())
    horizon = result.horizons[0]
    assert result.schema_version == "forecast_distribution.v1"
    assert result.primary_model == "bootstrap"
    assert horizon.horizon_days == 21
    assert horizon.baseline_status == "leads"
    assert horizon.calibration_status == "pending"
    assert horizon.confidence is None
    assert horizon.expected_return == pytest.approx(655 / 650 - 1)


def test_prediction_lab_payload_is_enriched_without_breaking_legacy_shape():
    payload = prediction_lab_payload()
    enriched = _attach_canonical_contract(payload)
    assert enriched["models"] == payload["models"]
    assert enriched["forecast_distribution"]["schema_version"] == (
        "forecast_distribution.v1"
    )


def test_stockpredictor_adapter_keeps_confidence_separate_from_calibration():
    result = from_stockpredictor(stockpredictor_payload())
    horizon = result.horizons[0]
    assert horizon.horizon_days == 10
    assert horizon.confidence == 0.71
    assert horizon.calibration_status == "calibrated"
    assert "not probability calibration" in horizon.limitations[0]


def test_stockpredictor_adapter_defaults_to_unknown_calibration():
    payload = stockpredictor_payload()
    del payload["calibration_status"]
    assert from_stockpredictor(payload).horizons[0].calibration_status == "unknown"


def test_stockpredictor_rejects_inconsistent_direction_probabilities():
    payload = stockpredictor_payload()
    payload["predictions"]["h10"]["probability_down"] = 0.5
    with pytest.raises(ForecastAdapterError, match=r"P\(up\)"):
        from_stockpredictor(payload)


def test_stockpredictor_rejects_price_return_mismatch():
    payload = stockpredictor_payload()
    payload["predictions"]["h10"]["predicted_price"] = 280.0
    with pytest.raises(ForecastAdapterError, match="inconsistent"):
        from_stockpredictor(payload)


def test_quantiles_must_be_ordered():
    with pytest.raises(ValidationError, match="monotonically ordered"):
        PriceQuantiles(p10=100, p50=90, p90=110)
