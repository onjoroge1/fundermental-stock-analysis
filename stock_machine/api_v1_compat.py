"""Compatibility shim for the agent API forecast reader.

Prediction payloads currently retain the legacy keyed `horizons` mapping while
also attaching the canonical `forecast_distribution.v1` contract, whose
`horizons` field is a list. The v1 agent routes need to understand both until
all persisted forecasts have migrated to one representation.

This module also mounts the operational market-data freshness endpoints on the
already-installed v1 router, keeping the main web app read-mostly while giving
operators an auditable health surface and a guarded lightweight refresh path.
"""
from __future__ import annotations

import hmac
from typing import Any

from fastapi import Header, HTTPException

from . import api_v1 as _base
from . import db

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


def _bearer(authorization: str | None) -> str:
    prefix = "Bearer "
    if authorization and authorization.startswith(prefix):
        return authorization[len(prefix):]
    return ""


def _require_admin(authorization: str | None) -> None:
    from .control_plane import admin_token

    expected = admin_token()
    if len(expected) < 24:
        raise HTTPException(503, "STOCK_MACHINE_ADMIN_TOKEN is not configured")
    if not hmac.compare_digest(_bearer(authorization), expected):
        raise HTTPException(401, "invalid admin credentials")


@_base.router.get("/data-health")
def market_data_health(max_age_hours: float = 18.0) -> dict:
    """Read-only freshness state for every covered daily-price dataset."""
    from .market_health import health

    threshold = max(1.0, min(float(max_age_hours), 168.0))
    with db.connect() as conn:
        return health(conn, max_age_hours=threshold)


@_base.router.post("/data-refresh")
def market_data_refresh(
    tickers: str = "",
    max_age_hours: float = 18.0,
    force: bool = False,
    limit: int = 10,
    authorization: str | None = Header(default=None),
) -> dict:
    """Admin-only lightweight price refresh; never runs the full data pipeline."""
    from .market_health import refresh_prices

    _require_admin(authorization)
    threshold = max(1.0, min(float(max_age_hours), 168.0))
    bounded_limit = max(1, min(int(limit), 25))
    requested = [x.strip().upper() for x in tickers.split(",") if x.strip()]

    with db.connect() as conn:
        if not requested:
            requested = [c["ticker"] for c in db.list_companies(conn)]
        return refresh_prices(
            conn,
            requested,
            only_if_stale=not force,
            max_age_hours=threshold,
            limit=bounded_limit,
        )


# Route functions are defined in api_v1 and resolve these globals at request
# time, so replacing them here keeps the mounted router compatible without
# duplicating endpoint implementations.
_base._prediction_horizon = _prediction_horizon
_base.bearish_asymmetry_score = bearish_asymmetry_score

router = _base.router
bear_strategy_guidance = _base.bear_strategy_guidance
