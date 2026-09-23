"""Versioned boundaries for analytics, simulation and research workers.

Prices are not returns. Availability is not an accounting period. Legacy
simulation timestamps are preserved, never certified as prospective.
"""
from __future__ import annotations
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator


def decimal_number(value):
    if isinstance(value, bool):
        raise ValueError("BOOLEAN_IS_NOT_MONEY")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("NON_FINITE_NUMBER")
    return number


Number = Annotated[Decimal, BeforeValidator(decimal_number)]
Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:/-]+$")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def aware_utc(cls, value):
        if isinstance(value, datetime):
            if value.utcoffset() is None:
                raise ValueError("TIMEZONE_REQUIRED")
            return value.astimezone(timezone.utc)
        return value


class Instrument(Contract):
    instrument_id: Identifier
    symbol: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9.-]{0,19}$")]
    asset_class: Literal["STOCK", "OPTION"]
    currency: Literal["USD"] = "USD"
    multiplier: Number = Decimal(1)
    expiration: date | None = None
    strike: Number | None = None
    right: Literal["C", "P"] | None = None
    deliverable: str | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.multiplier <= 0:
            raise ValueError("MULTIPLIER_MUST_BE_POSITIVE")
        if self.asset_class == "STOCK":
            if self.multiplier != 1 or any(v is not None for v in (self.expiration, self.strike, self.right, self.deliverable)):
                raise ValueError("STOCK_CONTRACT_INVALID")
        elif (not self.expiration or self.strike is None or self.strike <= 0
              or not self.right or not self.deliverable):
            raise ValueError("OPTION_IDENTITY_INCOMPLETE")
        return self


class SourceStamp(Contract):
    source_id: Identifier
    content_sha256: Hash
    effective_at: datetime
    available_at: datetime
    ingested_at: datetime
    availability_basis: Literal["OBSERVED", "LICENSED_ARCHIVE"]

    @model_validator(mode="after")
    def chronology(self):
        if not self.effective_at <= self.available_at <= self.ingested_at:
            raise ValueError("SOURCE_CHRONOLOGY_INVALID")
        return self


class DecisionContext(Contract):
    schema_version: Literal["integration-decision.v1"] = "integration-decision.v1"
    decision_id: Identifier
    portfolio_id: Identifier
    mode: Literal["PAPER", "SHADOW"]
    policy_version: Identifier
    engine_version: Identifier
    snapshot_sha256: Hash
    decision_at: datetime
    earliest_execution_at: datetime
    inputs: tuple[SourceStamp, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def no_lookahead(self):
        if self.earliest_execution_at <= self.decision_at:
            raise ValueError("EXECUTION_MUST_FOLLOW_DECISION")
        if any(x.ingested_at > self.decision_at for x in self.inputs):
            raise ValueError("INPUT_NOT_KNOWN_AT_DECISION")
        return self


class Candidate(Contract):
    schema_version: Literal["integration-candidate.v1"] = "integration-candidate.v1"
    candidate_id: Identifier
    decision_id: Identifier
    instrument: Instrument | None
    action: Literal["BUY", "SELL", "HOLD", "NO_TRADE"]
    eligible: bool = Field(strict=True)
    blockers: tuple[str, ...] = ()
    explanations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    max_loss_usd: Number | None = None

    @model_validator(mode="after")
    def eligibility(self):
        if self.eligible and self.blockers:
            raise ValueError("ELIGIBILITY_CONFLICT")
        if self.action != "NO_TRADE" and self.instrument is None:
            raise ValueError("INSTRUMENT_REQUIRED")
        if self.max_loss_usd is not None and self.max_loss_usd < 0:
            raise ValueError("NEGATIVE_MAX_LOSS")
        return self


class RiskReview(Contract):
    candidate_id: Identifier
    policy_version: Identifier
    portfolio_version: Hash
    assessed_at: datetime
    approved: bool = Field(strict=True)
    approved_quantity: Number = Decimal(0)
    reserved_capital_usd: Number = Decimal(0)
    blockers: tuple[str, ...] = ()

    @model_validator(mode="after")
    def approval(self):
        if self.approved_quantity < 0 or self.reserved_capital_usd < 0:
            raise ValueError("NEGATIVE_RISK_RESERVATION")
        if self.approved and (self.blockers or self.approved_quantity <= 0):
            raise ValueError("INVALID_RISK_APPROVAL")
        if not self.approved and (self.approved_quantity or self.reserved_capital_usd):
            raise ValueError("REJECTED_RISK_HAS_RESERVATION")
        return self


class ExecutionEvent(Contract):
    schema_version: Literal["portfolio-event.v1"] = "portfolio-event.v1"
    event_id: Identifier
    portfolio_id: Identifier
    engine_version: Identifier
    policy_version: Identifier
    source_sha256: Hash
    occurred_at: datetime
    recorded_at: datetime
    kind: Literal["CASH", "FILL", "FEE", "INCOME", "COLLATERAL", "SPLIT", "MARK"]
    quality: Literal["PROSPECTIVE_SIMULATION", "LEGACY_SIMULATION"] = "PROSPECTIVE_SIMULATION"
    instrument: Instrument | None = None
    decision_id: Identifier | None = None
    intent_id: Identifier | None = None
    decision_at: datetime | None = None
    quantity: Number = Decimal(0)
    price: Number = Decimal(0)
    amount: Number = Decimal(0)
    fee: Number = Decimal(0)
    split_factor: Number = Decimal(1)
    price_basis: Literal["RAW", "LEGACY_ADJUSTED"] = "RAW"

    @model_validator(mode="after")
    def valid_event(self):
        if self.occurred_at > self.recorded_at:
            raise ValueError("EVENT_FROM_FUTURE")
        if self.price < 0 or self.fee < 0 or self.split_factor <= 0:
            raise ValueError("INVALID_EVENT_NUMBER")
        if self.kind in {"FILL", "MARK", "SPLIT"} and self.instrument is None:
            raise ValueError("EVENT_INSTRUMENT_REQUIRED")
        if self.kind == "FILL":
            if not self.quantity or not self.decision_id or not self.intent_id or not self.decision_at:
                raise ValueError("FILL_LINKAGE_REQUIRED")
            if self.quality == "PROSPECTIVE_SIMULATION" and self.occurred_at <= self.decision_at:
                raise ValueError("FILL_PRECEDES_DECISION")
        if self.kind != "FILL" and (self.quantity or self.fee):
            raise ValueError("FILL_FIELDS_ON_NON_FILL")
        if self.kind in {"FILL", "MARK", "SPLIT"} and self.amount:
            raise ValueError("CASH_FIELD_ON_NON_CASH_EVENT")
        if self.kind == "FEE" and self.amount < 0:
            raise ValueError("FEE_AMOUNT_MUST_BE_POSITIVE")
        if self.price_basis == "LEGACY_ADJUSTED" and self.quality != "LEGACY_SIMULATION":
            raise ValueError("ADJUSTED_EXECUTION_IS_LEGACY_ONLY")
        if self.instrument and self.instrument.asset_class == "OPTION" and self.price_basis != "RAW":
            raise ValueError("OPTIONS_REQUIRE_RAW_PRICES")
        if self.kind == "SPLIT" and self.instrument.asset_class != "STOCK":
            raise ValueError("OPTION_DELIVERABLE_REQUIRES_EXPLICIT_NEW_CONTRACT")
        return self


def canonical(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()
