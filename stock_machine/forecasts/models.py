"""Canonical probabilistic forecast contract.

Every model must cross this boundary before its output can influence strategy
generation. The contract distinguishes model confidence from empirical
calibration and records whether a model beat its declared baseline.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CalibrationStatus = Literal["calibrated", "pending", "uncalibrated", "unknown"]
BaselineStatus = Literal["leads", "beats_baseline", "failed", "not_compared"]
CentralEstimateMethod = Literal["mean", "median", "model_output"]
ReadinessStatus = Literal["VALIDATED", "DIAGNOSTIC", "PENDING"]


class PriceQuantiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p10: float = Field(gt=0)
    p25: float | None = Field(default=None, gt=0)
    p50: float = Field(gt=0)
    p75: float | None = Field(default=None, gt=0)
    p90: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "PriceQuantiles":
        values = [self.p10]
        if self.p25 is not None:
            values.append(self.p25)
        values.append(self.p50)
        if self.p75 is not None:
            values.append(self.p75)
        values.append(self.p90)
        if values != sorted(values):
            raise ValueError("price quantiles must be monotonically ordered")
        return self


class ReturnQuantiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p10: float
    p25: float | None = None
    p50: float
    p75: float | None = None
    p90: float

    @model_validator(mode="after")
    def ordered(self) -> "ReturnQuantiles":
        values = [self.p10]
        if self.p25 is not None:
            values.append(self.p25)
        values.append(self.p50)
        if self.p75 is not None:
            values.append(self.p75)
        values.append(self.p90)
        if values != sorted(values):
            raise ValueError("return quantiles must be monotonically ordered")
        return self


class ForecastHorizon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horizon_days: int = Field(gt=0)
    probability_up: float = Field(ge=0, le=1)
    expected_return: float
    expected_price: float = Field(gt=0)
    expected_return_method: CentralEstimateMethod
    price_quantiles: PriceQuantiles
    return_quantiles: ReturnQuantiles
    confidence: float | None = Field(default=None, ge=0, le=1)
    calibration_status: CalibrationStatus
    calibration_samples: int = Field(default=0, ge=0)
    validation_samples: int = Field(default=0, ge=0)
    baseline_status: BaselineStatus
    readiness_status: ReadinessStatus = "DIAGNOSTIC"
    readiness_reasons: list[str] = Field(default_factory=list)
    model_name: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

class ForecastDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["forecast_distribution.v1"] = (
        "forecast_distribution.v1"
    )
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,20}$")
    as_of: date
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    spot_price: float = Field(gt=0)
    source: str = Field(min_length=1)
    primary_model: str = Field(min_length=1)
    readiness_status: ReadinessStatus = "DIAGNOSTIC"
    readiness_reasons: list[str] = Field(default_factory=list)
    horizons: list[ForecastHorizon] = Field(min_length=1)
    methodology: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_horizons(self) -> "ForecastDistribution":
        days = [h.horizon_days for h in self.horizons]
        if len(days) != len(set(days)):
            raise ValueError("forecast horizon days must be unique")
        self.horizons.sort(key=lambda h: h.horizon_days)
        return self
