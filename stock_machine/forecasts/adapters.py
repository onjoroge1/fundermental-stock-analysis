"""Adapters from model-specific payloads to the canonical forecast contract."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import (
    BaselineStatus,
    CalibrationStatus,
    ForecastDistribution,
    ForecastHorizon,
    PriceQuantiles,
    ReturnQuantiles,
)


class ForecastAdapterError(ValueError):
    """Raised when a model payload cannot satisfy the canonical contract."""


def _returns_from_prices(quantiles: PriceQuantiles, spot: float) -> ReturnQuantiles:
    def convert(value: float | None) -> float | None:
        return None if value is None else value / spot - 1.0

    return ReturnQuantiles(
        p10=convert(quantiles.p10),
        p25=convert(quantiles.p25),
        p50=convert(quantiles.p50),
        p75=convert(quantiles.p75),
        p90=convert(quantiles.p90),
    )


def _coerce_status(value: str | None, allowed: set[str], default: str) -> str:
    normalized = (value or "").lower()
    return normalized if normalized in allowed else default


def from_prediction_lab(payload: dict[str, Any]) -> ForecastDistribution:
    """Adapt the repository's bootstrap/LSTM prediction-lab payload."""
    if payload.get("status") != "OK":
        raise ForecastAdapterError("prediction-lab payload status is not OK")

    try:
        symbol = str(payload["ticker"]).upper()
        spot = float(payload["last_price"])
        primary = str(payload["primary_model"])
        model_payload = payload["models"][primary]
        raw_horizons = model_payload["horizons"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ForecastAdapterError(
            f"prediction-lab payload is missing required data: {exc}"
        ) from exc

    verdict = (payload.get("validation") or {}).get("verdict") or {}
    if primary in {"lstm", "bootstrap"} and verdict.get("forecast_edge"):
        baseline_status: BaselineStatus = "beats_baseline"
    elif primary == "bootstrap_drift_neutral":
        baseline_status = "leads"
    else:
        baseline_status = "failed"

    horizons: list[ForecastHorizon] = []
    for label, raw in raw_horizons.items():
        try:
            prices = PriceQuantiles(
                p10=float(raw["p10"]),
                p25=float(raw["p25"]),
                p50=float(raw["p50"]),
                p75=float(raw["p75"]),
                p90=float(raw["p90"]),
            )
            returns = _returns_from_prices(prices, spot)
            limitations = [f"source horizon label: {label}"]
            calibration = _coerce_status(
                raw.get("calibration_status"),
                {"calibrated", "pending", "uncalibrated", "unknown"},
                "pending",
            )
            if calibration != "calibrated":
                limitations.insert(0, "probability is model-implied and not calibrated")
            median_up = prices.p50 >= spot
            probability_up = float(raw["prob_positive"])
            if (probability_up >= 0.5) != median_up:
                limitations.append(
                    "P(up) and median-return direction disagree; treat direction as low confidence"
                )
            horizons.append(
                ForecastHorizon(
                    horizon_days=int(raw["days"]),
                    probability_up=probability_up,
                    expected_return=returns.p50,
                    expected_price=prices.p50,
                    expected_return_method="median",
                    price_quantiles=prices,
                    return_quantiles=returns,
                    confidence=None,
                    calibration_status=calibration,
                    baseline_status=baseline_status,
                    model_name=primary,
                    limitations=limitations,
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ForecastAdapterError(
                f"invalid prediction-lab horizon {label!r}: {exc}"
            ) from exc

    methodology = dict(payload.get("methodology") or {})
    return ForecastDistribution(
        symbol=symbol,
        as_of=str(payload["as_of"])[:10],
        spot_price=spot,
        source="stock_machine.prediction_lab",
        primary_model=primary,
        horizons=horizons,
        methodology=methodology,
        limitations=[methodology.get("limitations", "")]
        if methodology.get("limitations")
        else [],
    )


def from_stockpredictor(payload: dict[str, Any]) -> ForecastDistribution:
    """Adapt the attached LightGBM/conformal 5/10/20-day output.

    A confidence score is preserved as model metadata; it is never promoted to
    calibrated probability unless the source explicitly declares calibration.
    """
    try:
        symbol = str(payload.get("symbol") or payload["ticker"]).upper()
        spot = float(payload["current_price"])
        predictions = payload["predictions"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ForecastAdapterError(
            f"StockPredictor payload is missing required data: {exc}"
        ) from exc

    root_calibration = payload.get("calibration_status")
    root_baseline = payload.get("baseline_status")
    horizons: list[ForecastHorizon] = []
    for label, raw in predictions.items():
        if not label.startswith("h") or not isinstance(raw, dict):
            continue
        try:
            days = int(label[1:])
            probability_up = float(raw["probability_up"])
            probability_down = raw.get("probability_down")
            if probability_down is not None:
                total = probability_up + float(probability_down)
                if abs(total - 1.0) > 0.01:
                    raise ForecastAdapterError(
                        f"{label} P(up) + P(down) must equal 1; got {total}"
                    )

            expected_return = float(raw["expected_return"])
            expected_price = float(
                raw.get("predicted_price", spot * (1.0 + expected_return))
            )
            derived_price = spot * (1.0 + expected_return)
            if abs(expected_price - derived_price) > max(0.02, spot * 0.001):
                raise ForecastAdapterError(
                    f"{label} predicted price is inconsistent with expected return"
                )
            prices = PriceQuantiles(
                p10=float(raw["band_p10"]),
                p25=(float(raw["band_p25"])
                     if raw.get("band_p25") is not None else None),
                p50=float(raw.get("band_p50", expected_price)),
                p75=(float(raw["band_p75"])
                     if raw.get("band_p75") is not None else None),
                p90=float(raw["band_p90"]),
            )
            if all(raw.get(key) is not None
                   for key in ("ret_p10", "ret_p50", "ret_p90")):
                returns = ReturnQuantiles(
                    p10=float(raw["ret_p10"]),
                    p25=(float(raw["ret_p25"])
                         if raw.get("ret_p25") is not None else None),
                    p50=float(raw["ret_p50"]),
                    p75=(float(raw["ret_p75"])
                         if raw.get("ret_p75") is not None else None),
                    p90=float(raw["ret_p90"]),
                )
            else:
                returns = _returns_from_prices(prices, spot)

            calibration = _coerce_status(
                raw.get("calibration_status") or root_calibration,
                {"calibrated", "pending", "uncalibrated", "unknown"},
                "unknown",
            )
            baseline = _coerce_status(
                raw.get("baseline_status") or root_baseline,
                {"leads", "beats_baseline", "failed", "not_compared"},
                "not_compared",
            )
            horizons.append(
                ForecastHorizon(
                    horizon_days=days,
                    probability_up=probability_up,
                    expected_return=expected_return,
                    expected_price=expected_price,
                    expected_return_method="model_output",
                    price_quantiles=prices,
                    return_quantiles=returns,
                    confidence=(float(raw["confidence"])
                                if raw.get("confidence") is not None else None),
                    calibration_status=calibration,
                    baseline_status=baseline,
                    model_name=str(raw.get("model_name", "lightgbm_conformal")),
                    limitations=[
                        "confidence is a model score, not probability calibration"
                    ],
                )
            )
        except ForecastAdapterError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ForecastAdapterError(
                f"invalid StockPredictor horizon {label!r}: {exc}"
            ) from exc

    if not horizons:
        raise ForecastAdapterError("StockPredictor payload has no hN horizons")

    return ForecastDistribution(
        symbol=symbol,
        as_of=(str(payload["as_of"])[:10] if payload.get("as_of")
               else datetime.now(timezone.utc).date()),
        generated_at=payload.get("generated_at") or datetime.now(timezone.utc),
        spot_price=spot,
        source="stockpredictor.lightgbm_conformal",
        primary_model=str(payload.get("primary_model", "lightgbm_conformal")),
        horizons=horizons,
        methodology=dict(payload.get("methodology") or {}),
        limitations=list(payload.get("limitations") or []),
    )
