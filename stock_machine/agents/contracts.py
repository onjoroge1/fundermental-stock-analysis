"""Versioned, fail-closed contracts for the first Agent Lab milestone."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

PILOT = ("AAPL", "MSFT", "UBER", "HIMS", "VZ")
POLICY_ID = "research-pilot-v1"
SCHEMA_VERSION = "agent-journal.v1"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical(value: object) -> str:
    """No NaN, infinity, silently coerced numbers or fabricated serialization."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Policy(Frozen):
    policy_id: Literal["research-pilot-v1"] = POLICY_ID
    schema_version: Literal["agent-journal.v1"] = SCHEMA_VERSION
    mode: Literal["RESEARCH"] = "RESEARCH"
    tickers: tuple[str, ...] = PILOT
    evaluation_horizon_sessions: Literal[20] = 20
    order_submission: Literal[False] = False
    simulated_execution: Literal[False] = False
    exploration_enabled: Literal[False] = False
    reward_enabled: Literal[False] = False
    qualified_forward_paper: Literal[False] = False
    engine: Literal["deterministic_research_recorder.v1"] = "deterministic_research_recorder.v1"

    @model_validator(mode="after")
    def fixed_universe(self):
        if self.tickers != PILOT:
            raise ValueError("A universe change requires a new reviewed policy version")
        return self


class CaptureRequest(Frozen):
    idempotency_key: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")


class ReviewRequest(CaptureRequest):
    message: str = Field(min_length=1, max_length=2000)


class Decision(Frozen):
    schema_version: Literal["agent-journal.v1"] = SCHEMA_VERSION
    decision_id: str
    policy_id: Literal["research-pilot-v1"] = POLICY_ID
    ticker: str
    mode: Literal["RESEARCH"] = "RESEARCH"
    observed_at: AwareDatetime
    decided_at: AwareDatetime
    status: Literal["RECORDED", "BLOCKED", "FAILED"]
    action: Literal["WATCH", "NO_TRADE"]
    selection_mode: Literal["RULE_BASED"] = "RULE_BASED"
    action_probability: None = None
    execution_status: Literal["NOT_ENABLED"] = "NOT_ENABLED"
    reward: None = None
    pnl: None = None
    horizon_sessions: Literal[20] = 20
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rationale: str = Field(min_length=1, max_length=2000)
    source_thesis: str | None = Field(default=None, max_length=12000)
    source_counterargument: str | None = Field(default=None, max_length=12000)
    source_invalidation: str | None = Field(default=None, max_length=12000)
    evidence_pointers: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    source_model_status: str = "MISSING"
    source_model_version: str | None = None
    price_date: str | None = None
    research_snapshot_id: str | None = None
    source_report_id: str | None = None
    source_report_as_of: str | None = None
    research_contract_version: str | None = None
    previous_decision_id: str | None = None

    @model_validator(mode="after")
    def boundaries(self):
        if self.ticker not in PILOT:
            raise ValueError("Ticker is outside the frozen research pilot")
        if self.observed_at > self.decided_at:
            raise ValueError("Evidence must precede the decision")
        if self.decided_at > utc_now():
            raise ValueError("Future decisions are not accepted")
        if self.status in {"BLOCKED", "FAILED"} and (self.action != "NO_TRADE" or not self.blockers):
            raise ValueError("A blocked/failed decision must abstain and explain why")
        if self.status == "RECORDED" and (self.action != "WATCH" or self.blockers):
            raise ValueError("Recorded research can WATCH, never authorize a trade")
        return self
