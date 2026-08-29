"""Read-only option payoff, filtering, strategy generation and valuation."""

from .generator import GenerationPolicy, generate_strategies
from .forecast_io import load_latest_forecast
from .models import (
    OptionAction,
    OptionLeg,
    StrategyCandidate,
    StrategyGenerationResult,
    StrategyType,
)
from .extended import (
    ExtendedPolicy,
    ExtendedStrategyType,
    covered_call,
    mixed_expiration,
)
from .payoff import expiration_pnl, payoff_points, summarize_payoff

__all__ = [
    "ExtendedPolicy",
    "ExtendedStrategyType",
    "GenerationPolicy",
    "OptionAction",
    "OptionLeg",
    "StrategyCandidate",
    "StrategyGenerationResult",
    "StrategyType",
    "covered_call",
    "expiration_pnl",
    "generate_strategies",
    "load_latest_forecast",
    "mixed_expiration",
    "payoff_points",
    "summarize_payoff",
]
