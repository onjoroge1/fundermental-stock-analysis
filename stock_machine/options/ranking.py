"""Explainable heuristic ranking for option candidates."""
from __future__ import annotations

from datetime import date

from ..forecasts.models import ForecastDistribution, ForecastHorizon
from .models import (
    ForecastAssessment,
    LiquidityAssessment,
    PayoffSummary,
    RankingBreakdown,
    StrategyType,
)

_BULLISH = {
    StrategyType.CASH_SECURED_PUT,
    StrategyType.BULL_CALL_DEBIT_SPREAD,
    StrategyType.BULL_PUT_CREDIT_SPREAD,
}
_BEARISH = {
    StrategyType.BEAR_PUT_DEBIT_SPREAD,
    StrategyType.BEAR_CALL_CREDIT_SPREAD,
}


def _nearest_horizon(
    forecast: ForecastDistribution, days_to_expiration: int
) -> ForecastHorizon:
    return min(
        forecast.horizons,
        key=lambda horizon: abs(horizon.horizon_days - days_to_expiration),
    )


def assess_forecast(
    strategy_type: StrategyType,
    payoff: PayoffSummary,
    days_to_expiration: int,
    forecast: ForecastDistribution | None,
    *,
    symbol: str,
    spot_price: float,
    as_of: date,
) -> ForecastAssessment:
    """Score alignment without claiming to estimate probability of profit."""
    if forecast is None:
        return ForecastAssessment(
            available=False,
            score=0.5,
            warnings=["no forecast supplied; ranking uses a neutral alignment score"],
        )
    warnings: list[str] = []
    if forecast.symbol != symbol:
        return ForecastAssessment(
            available=False,
            score=0.5,
            warnings=["forecast symbol does not match option chain"],
        )
    if forecast.as_of > as_of:
        return ForecastAssessment(
            available=False,
            score=0.5,
            warnings=["forecast as-of date is after the market-data snapshot"],
        )
    forecast_age_days = (as_of - forecast.as_of).days
    spot_mismatch = abs(forecast.spot_price / spot_price - 1.0) > 0.05
    if spot_mismatch:
        warnings.append("forecast spot differs from option-chain spot by more than 5%")

    horizon = _nearest_horizon(forecast, days_to_expiration)
    if strategy_type in _BULLISH:
        directional = horizon.probability_up
    elif strategy_type in _BEARISH:
        directional = 1.0 - horizon.probability_up
    else:
        directional = 1.0 - abs(2.0 * horizon.probability_up - 1.0)

    p50 = horizon.price_quantiles.p50
    p10 = horizon.price_quantiles.p10
    p90 = horizon.price_quantiles.p90
    breakevens = payoff.breakevens
    range_alignment = 0.5
    if strategy_type in _BULLISH and breakevens:
        threshold = min(breakevens)
        range_alignment = 1.0 if p50 >= threshold else (0.65 if p90 >= threshold else 0.15)
    elif strategy_type in _BEARISH and breakevens:
        threshold = max(breakevens)
        range_alignment = 1.0 if p50 <= threshold else (0.65 if p10 <= threshold else 0.15)
    elif strategy_type == StrategyType.IRON_CONDOR and len(breakevens) == 2:
        lower, upper = breakevens
        if p10 >= lower and p90 <= upper:
            range_alignment = 1.0
        elif lower <= p50 <= upper:
            range_alignment = 0.6
        else:
            range_alignment = 0.1

    score = 0.6 * directional + 0.4 * range_alignment
    if horizon.calibration_status != "calibrated":
        score = 0.5 + (score - 0.5) * 0.6
        warnings.append(
            "forecast is not calibrated; alignment influence was reduced"
        )
    gap = abs(horizon.horizon_days - days_to_expiration)
    if gap > max(7, days_to_expiration * 0.5):
        score = 0.5 + (score - 0.5) * 0.7
        warnings.append("nearest forecast horizon is materially different from DTE")
    if forecast_age_days > 7:
        score = 0.5 + (score - 0.5) * 0.5
        warnings.append("forecast is more than seven calendar days old")
    if spot_mismatch:
        score = 0.5 + (score - 0.5) * 0.5
    return ForecastAssessment(
        available=True,
        horizon_days=horizon.horizon_days,
        forecast_age_days=forecast_age_days,
        calibration_status=horizon.calibration_status,
        directional_alignment=directional,
        range_alignment=range_alignment,
        score=max(0.0, min(1.0, score)),
        warnings=warnings,
    )


def rank_candidate(
    payoff: PayoffSummary,
    liquidity: LiquidityAssessment,
    forecast: ForecastAssessment,
) -> RankingBreakdown:
    """Return a transparent comparison score, never a return forecast."""
    risk_efficiency = 0.0
    if payoff.return_on_risk is not None:
        risk_efficiency = min(1.0, max(0.0, payoff.return_on_risk))
    if payoff.max_profit is None:
        risk_efficiency = max(risk_efficiency, 0.5)

    premium_efficiency = 0.0
    if payoff.max_profit is not None and payoff.max_loss is not None:
        total = payoff.max_profit + payoff.max_loss
        if total > 0:
            premium_efficiency = payoff.max_profit / total

    total = 100.0 * (
        0.35 * liquidity.score
        + 0.25 * risk_efficiency
        + 0.15 * premium_efficiency
        + 0.25 * forecast.score
    )
    return RankingBreakdown(
        liquidity=liquidity.score,
        risk_efficiency=risk_efficiency,
        premium_efficiency=premium_efficiency,
        forecast_alignment=forecast.score,
        total=round(total, 2),
    )
