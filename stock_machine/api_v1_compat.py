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


# Route functions are defined in api_v1 and resolve this global at request
# time, so replacing it here makes the mounted router compatible without
# duplicating the endpoint implementation.
_base._prediction_horizon = _prediction_horizon

router = _base.router
bearish_asymmetry_score = _base.bearish_asymmetry_score
bear_strategy_guidance = _base.bear_strategy_guidance
