from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from stock_machine.integrations.contracts import Instrument, SourceStamp, DecisionContext, Candidate, RiskReview, ExecutionEvent, digest

T = datetime(2020, 1, 2, 22, tzinfo=timezone.utc)
H = "a" * 64
STOCK = dict(instrument_id="STOCK:AAPL", symbol="AAPL", asset_class="STOCK")


def test_instrument_id_is_not_just_an_underlying_symbol():
    with pytest.raises(ValueError, match="OPTION_IDENTITY"):
        Instrument(**{**STOCK, "asset_class": "OPTION"})
    a = Instrument(**{**STOCK, "asset_class": "OPTION", "expiration": "2020-03-20", "strike": "100", "right": "C", "multiplier": 100, "deliverable": "100 AAPL"})
    assert a.multiplier == Decimal(100)
    with pytest.raises(ValueError, match="STOCK_CONTRACT"):
        Instrument(**STOCK, multiplier=100)


def source():
    return SourceStamp(source_id="SEC:test", content_sha256=H, effective_at=T-timedelta(days=1), available_at=T, ingested_at=T, availability_basis="OBSERVED")


def context(**kw):
    return dict(decision_id="d", portfolio_id="paper", mode="PAPER", policy_version="v2", engine_version="v1", snapshot_sha256=H, decision_at=T, earliest_execution_at=T+timedelta(hours=1), inputs=(source(),), **kw)


def test_snapshot_clock_and_strict_contract_version():
    valid = DecisionContext(**context())
    assert DecisionContext.model_validate_json(valid.model_dump_json()) == valid
    assert digest(valid) == digest(valid)
    with pytest.raises(ValueError, match="EXECUTION_MUST"):
        DecisionContext(**{**context(), "earliest_execution_at": T})
    with pytest.raises(ValueError, match="INPUT_NOT_KNOWN"):
        DecisionContext(**{**context(), "decision_at": T-timedelta(seconds=1)})
    with pytest.raises(ValueError):
        DecisionContext(**{**context(), "schema_version": "v999"})
    with pytest.raises(ValueError, match="TIMEZONE_REQUIRED"):
        DecisionContext(**{**context(), "decision_at": T.replace(tzinfo=None)})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "NaN", "Infinity"])
def test_money_rejects_invalid_numbers(value):
    with pytest.raises(ValueError):
        Instrument(**{**STOCK, "multiplier": value})


def test_risk_eligibility_is_explicit():
    with pytest.raises(ValueError, match="ELIGIBILITY_CONFLICT"):
        Candidate(candidate_id="c", decision_id="d", instrument=None, action="NO_TRADE", eligible=True, blockers=("STALE",))
    with pytest.raises(ValueError, match="INVALID_RISK_APPROVAL"):
        RiskReview(candidate_id="c", policy_version="v", portfolio_version=H, assessed_at=T, approved=True)


def test_fill_before_decision_requires_visible_legacy_quality():
    values = dict(event_id="e", portfolio_id="p", engine_version="v1", policy_version="v1", source_sha256=H,
                  occurred_at=T, recorded_at=T+timedelta(hours=1), kind="FILL", instrument=STOCK,
                  decision_id="d", intent_id="i", decision_at=T+timedelta(minutes=1), quantity=2, price=10)
    with pytest.raises(ValueError, match="FILL_PRECEDES"):
        ExecutionEvent(**values)
    event = ExecutionEvent(**values, quality="LEGACY_SIMULATION", price_basis="LEGACY_ADJUSTED")
    assert event.quality == "LEGACY_SIMULATION"
    assert ExecutionEvent.model_validate_json(event.model_dump_json()) == event
