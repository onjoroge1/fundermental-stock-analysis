"""Compatibility shim for the agent API forecast reader.

Prediction payloads currently retain the legacy keyed `horizons` mapping while
also attaching the canonical `forecast_distribution.v1` contract, whose
`horizons` field is a list.  The v1 agent routes need to understand both until
all persisted forecasts have migrated to one representation.
"""
from __future__ import annotations

from typing import Any

from . import api_v1 as _base

_HORIZON_DAYS = {
    "5d": 5,
    "10d": 10,
    "20d": 20,
    "1m": 21,
    "3m": 63,
    "6m": 126,
    "12m": 252,
}


def _prediction_horizon(
    prediction: dict[str, Any] | None,
    horizon: str,
) -> dict[str, Any]:
    if not prediction:
        return {}

    # Legacy contract contains tail probabilities used by the current options
    # stack, so prefer it when present.
    legacy = prediction.get("horizons") or {}
    if isinstance(legacy, dict):
        row = legacy.get(horizon)
        if isinstance(row, dict) and row:
            return row

    canonical = prediction.get("forecast_distribution") or {}
    rows = canonical.get("horizons") or []
    target_days = _HORIZON_DAYS.get(horizon)
    if isinstance(rows, list) and target_days is not None:
        for item in rows:
            if not isinstance(item, dict):
                continue
            if int(item.get("horizon_days") or 0) != target_days:
                continue
            normalized = dict(item)
            if "prob_positive" not in normalized:
                normalized["prob_positive"] = item.get("probability_up")
            return normalized
    return {}


def bearish_asymmetry_score(
    *,
    expected_return_pct: float | None,
    bear_downside_pct: float | None,
    bull_upside_pct: float | None,
    quality_score: float | None,
    classification: str | None,
) -> float | None:
    """Harden the base score so missing bull data never earns upside credit."""
    if expected_return_pct is None or bear_downside_pct is None:
        return None

    negative_er = _base._clamp((-float(expected_return_pct)) / 40.0) * 40.0
    downside = _base._clamp((-float(bear_downside_pct)) / 60.0) * 30.0
    bull_ceiling = 0.0
    if bull_upside_pct is not None:
        bull_ceiling = _base._clamp((25.0 - float(bull_upside_pct)) / 25.0) * 20.0

    quality = 70.0 if quality_score is None else float(quality_score)
    fragility = _base._clamp((70.0 - quality) / 40.0) * 5.0
    label = 5.0 if str(classification or "").upper() == "UNATTRACTIVE" else 0.0
    return round(
        _base._clamp(
            negative_er + downside + bull_ceiling + fragility + label,
            0.0,
            100.0,
        ),
        1,
    )


# Route functions are defined in api_v1 and resolve these globals at request
# time, so replacing them here keeps the mounted router compatible without
# duplicating endpoint implementations.
_base._prediction_horizon = _prediction_horizon
_base.bearish_asymmetry_score = bearish_asymmetry_score

router = _base.router
bear_strategy_guidance = _base.bear_strategy_guidance
