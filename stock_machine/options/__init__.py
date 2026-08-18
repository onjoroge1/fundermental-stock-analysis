"""Read-only option payoff, filtering, and strategy generation."""

from .generator import GenerationPolicy, generate_strategies
from .forecast_io import load_latest_forecast
from .models import (
    OptionAction,
    OptionLeg,
    StrategyCandidate,
    StrategyGenerationResult,
    StrategyType,
)
from .payoff import expiration_pnl, payoff_points, summarize_payoff

__all__ = [
    "GenerationPolicy",
    "OptionAction",
    "OptionLeg",
    "StrategyCandidate",
    "StrategyGenerationResult",
    "StrategyType",
    "expiration_pnl",
    "generate_strategies",
    "load_latest_forecast",
    "payoff_points",
    "summarize_payoff",
]
