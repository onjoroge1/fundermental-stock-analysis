"""Contracts for deterministic option payoff and strategy analysis."""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..forecasts.models import CalibrationStatus
from ..market_data.models import MarketDataAvailability, OptionContract


class OptionAction(StrEnum):
    BUY = "buy"
    SELL = "sell"


class StrategyType(StrEnum):
    CASH_SECURED_PUT = "cash_secured_put"
    BULL_CALL_DEBIT_SPREAD = "bull_call_debit_spread"
    BEAR_PUT_DEBIT_SPREAD = "bear_put_debit_spread"
    BULL_PUT_CREDIT_SPREAD = "bull_put_credit_spread"
    BEAR_CALL_CREDIT_SPREAD = "bear_call_credit_spread"
    IRON_CONDOR = "iron_condor"


class OptionLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: OptionContract
    action: OptionAction
    quantity: int = Field(default=1, ge=1, le=100)
    entry_price: float = Field(ge=0)
    price_basis: Literal["natural", "mid", "manual"] = "natural"

    @property
    def signed_quantity(self) -> int:
        return self.quantity if self.action == OptionAction.BUY else -self.quantity


class PayoffSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    net_credit: float
    max_profit: float | None = Field(default=None, ge=0)
    max_loss: float | None = Field(default=None, ge=0)
    collateral_estimate: float | None = Field(default=None, ge=0)
    breakevens: list[float] = Field(default_factory=list)
    return_on_risk: float | None = None
    defined_risk: bool
    warnings: list[str] = Field(default_factory=list)


class PayoffPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    underlying_price: float = Field(ge=0)
    profit_loss: float


class PositionGreeks(BaseModel):
    """First-order position Greeks in conventional one-contract units."""

    model_config = ConfigDict(extra="forbid")

    net_delta_shares: float | None = None
    net_gamma: float | None = None
    net_theta_per_day: float | None = None
    net_vega: float | None = None
    complete: bool
    warnings: list[str] = Field(default_factory=list)


class LiquidityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    score: float = Field(ge=0, le=1)
    max_relative_spread: float | None = Field(default=None, ge=0)
    minimum_open_interest: float | None = Field(default=None, ge=0)
    minimum_volume: float | None = Field(default=None, ge=0)
    availability: list[MarketDataAvailability] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ForecastAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    horizon_days: int | None = Field(default=None, gt=0)
    forecast_age_days: int | None = Field(default=None, ge=0)
    calibration_status: CalibrationStatus | None = None
    directional_alignment: float | None = Field(default=None, ge=0, le=1)
    range_alignment: float | None = Field(default=None, ge=0, le=1)
    score: float = Field(default=0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class RankingBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    liquidity: float = Field(ge=0, le=1)
    risk_efficiency: float = Field(ge=0, le=1)
    premium_efficiency: float = Field(ge=0, le=1)
    forecast_alignment: float = Field(ge=0, le=1)
    total: float = Field(ge=0, le=100)
    methodology: str = (
        "heuristic comparison score; not expected return or probability of profit"
    )


class StrategyCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["option_strategy_candidate.v1"] = (
        "option_strategy_candidate.v1"
    )
    candidate_id: str = Field(min_length=1)
    strategy_type: StrategyType
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,20}$")
    spot_price: float = Field(gt=0)
    expiration: date
    days_to_expiration: int = Field(ge=0)
    legs: list[OptionLeg] = Field(min_length=1, max_length=4)
    payoff: PayoffSummary
    expiration_payoff_points: list[PayoffPoint] = Field(min_length=2)
    position_greeks: PositionGreeks
    liquidity: LiquidityAssessment
    forecast: ForecastAssessment
    ranking: RankingBreakdown
    rationale: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def contracts_are_coherent(self) -> "StrategyCandidate":
        symbols = {leg.contract.symbol for leg in self.legs}
        expirations = {leg.contract.expiration for leg in self.legs}
        if symbols != {self.symbol}:
            raise ValueError("all option legs must match the candidate symbol")
        if expirations != {self.expiration}:
            raise ValueError("Phase 3 candidates require one common expiration")
        return self


class RejectedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_type: StrategyType | None = None
    contracts: list[int] = Field(default_factory=list)
    reasons: list[str] = Field(min_length=1)


class StrategyGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["option_strategy_generation.v1"] = (
        "option_strategy_generation.v1"
    )
    symbol: str
    as_of: datetime
    candidates: list[StrategyCandidate]
    rejected: list[RejectedCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    methodology: list[str] = Field(default_factory=list)
