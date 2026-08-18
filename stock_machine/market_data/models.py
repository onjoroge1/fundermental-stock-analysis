"""Canonical market-data contracts for stocks and listed options."""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MarketDataAvailability(StrEnum):
    REALTIME = "realtime"
    DELAYED = "delayed"
    FROZEN = "frozen"
    FROZEN_DELAYED = "frozen_delayed"
    NOT_SUBSCRIBED = "not_subscribed"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


class SessionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    connected: bool
    authenticated: bool
    competing: bool = False
    message: str = ""
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class UnderlyingContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,20}$")
    conid: int = Field(gt=0)
    name: str | None = None
    currency: str = "USD"
    exchange: str | None = None
    has_options: bool = False
    option_months: list[str] = Field(default_factory=list)


class MarketQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    conid: int = Field(gt=0)
    symbol: str | None = None
    as_of: datetime
    availability: MarketDataAvailability
    bid: float | None = Field(default=None, ge=0)
    ask: float | None = Field(default=None, ge=0)
    last: float | None = Field(default=None, ge=0)
    mark: float | None = Field(default=None, ge=0)
    bid_size: float | None = Field(default=None, ge=0)
    ask_size: float | None = Field(default=None, ge=0)
    last_size: float | None = Field(default=None, ge=0)
    volume: float | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def flag_crossed_or_empty_quote(self) -> "MarketQuote":
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            self.warnings.append("crossed quote: bid exceeds ask")
        if all(value is None for value in (self.bid, self.ask, self.last, self.mark)):
            self.warnings.append("no price fields returned")
        return self


class StrikeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    underlying: UnderlyingContract
    month: str = Field(pattern=r"^[A-Z]{3}\d{2}$")
    call_strikes: list[float]
    put_strikes: list[float]
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class OptionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    conid: int = Field(gt=0)
    symbol: str = Field(pattern=r"^[A-Z0-9.\-]{1,20}$")
    underlying_conid: int = Field(gt=0)
    expiration: date
    strike: float = Field(gt=0)
    right: Literal["C", "P"]
    multiplier: int = Field(default=100, gt=0)
    currency: str = "USD"
    exchange: str = "SMART"
    description: str | None = None


class OptionQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: OptionContract
    quote: MarketQuote
    implied_volatility: float | None = Field(default=None, ge=0)
    delta: float | None = Field(default=None, ge=-1, le=1)
    gamma: float | None = Field(default=None, ge=0)
    theta: float | None = None
    vega: float | None = Field(default=None, ge=0)
    open_interest: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def conids_match(self) -> "OptionQuote":
        if self.contract.conid != self.quote.conid:
            raise ValueError("option contract and quote conids must match")
        return self


class OptionChainSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["option_chain_snapshot.v1"] = (
        "option_chain_snapshot.v1"
    )
    provider: str
    underlying: UnderlyingContract
    underlying_quote: MarketQuote
    month: str = Field(pattern=r"^[A-Z]{3}\d{2}$")
    options: list[OptionQuote]
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    warnings: list[str] = Field(default_factory=list)
