from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pytest
from stock_machine.integrations.contracts import ExecutionEvent
from stock_machine.integrations.ledger import Ledger, project

T = datetime(2020, 1, 2, 20, tzinfo=timezone.utc)
I = dict(instrument_id="STOCK:AAPL", symbol="AAPL", asset_class="STOCK")


def event(key, kind, **changes):
    values = dict(event_id=key, portfolio_id="paper", engine_version="fixture-v1", policy_version="p1", source_sha256="a"*64,
                  occurred_at=T, recorded_at=T+timedelta(hours=1), kind=kind)
    if kind in {"FILL", "MARK", "SPLIT"}: values["instrument"] = I
    if kind == "FILL": values.update(decision_id="d", intent_id="i", decision_at=T-timedelta(minutes=1))
    return ExecutionEvent(**{**values, **changes})


def test_cash_deposits_not_profit_and_cost_charged_once():
    cash, buy = event("cash", "CASH", amount=1000), event("buy", "FILL", quantity=2, price=100, fee=1)
    value = project([cash, buy, buy, event("mark", "MARK", price=110)], "2020-01-02")
    assert Decimal(value["equity_usd"]) == 1019
    assert Decimal(value["net_pnl_usd"]) == 19
    assert value["event_count"] == 3
    assert Decimal(value["fees_usd"]) == 1


def test_short_round_trip_cash_and_pnl():
    value = project([event("cash", "CASH", amount=1000), event("sell", "FILL", quantity=-2, price=100, fee=1),
                     event("cover", "FILL", quantity=2, price=80, fee=1)], "2020-01-02")
    assert Decimal(value["cash_usd"]) == 1038
    assert Decimal(value["realized_gross_pnl_usd"]) == 40
    assert value["positions"] == []


def test_partial_close_then_reversal_uses_new_cost_basis():
    ledger = Ledger()
    for e in [event("cash", "CASH", amount=1000), event("buy", "FILL", quantity=2, price=100),
              event("sell", "FILL", quantity=-3, price=120), event("mark", "MARK", price=110)]: ledger.apply(e)
    value = ledger.snapshot("2020-01-02")
    assert Decimal(value["realized_gross_pnl_usd"]) == 40
    assert Decimal(value["unrealized_pnl_usd"]) == 10
    assert Decimal(value["equity_usd"]) == 1050


def test_options_use_multiplier_and_income_is_not_external_flow():
    option = {**I, "instrument_id": "OPTION:AAPL:C100:20200320", "asset_class": "OPTION", "expiration": "2020-03-20", "strike": 100, "right": "C", "multiplier": 100, "deliverable": "100 AAPL"}
    value = project([event("cash", "CASH", amount=1000), event("buy", "FILL", instrument=option, quantity=1, price=3, fee=1),
                     event("mark", "MARK", instrument=option, price=4), event("income", "INCOME", amount=2)], "2020-01-02")
    assert Decimal(value["equity_usd"]) == 1101
    assert Decimal(value["external_flows_usd"]) == 1000


def test_split_and_collateral_do_not_create_profit():
    value = project([event("cash", "CASH", amount=1000), event("buy", "FILL", quantity=2, price=100),
                     event("split", "SPLIT", split_factor=2), event("reserve", "COLLATERAL", amount=100)], "2020-01-02")
    assert Decimal(value["equity_usd"]) == 1000
    assert Decimal(value["available_cash_usd"]) == 700
    assert Decimal(value["positions"][0]["quantity"]) == 4


def test_missing_mark_withholds_valuation_instead_of_zero_or_forward_fill():
    value = project([event("cash", "CASH", amount=1000), event("buy", "FILL", quantity=2, price=100)], "2020-01-03")
    assert value["status"] == "WITHHELD" and value["equity_usd"] is None
    assert value["missing_or_stale_marks"] == ["STOCK:AAPL"]


def test_conflicts_and_mixed_books_rejected():
    with pytest.raises(ValueError, match="CONTENT_CONFLICT"):
        project([event("c", "CASH", amount=100), event("c", "CASH", amount=200)], "2020-01-02")
    with pytest.raises(ValueError, match="MUST_BE_SEPARATE"):
        project([event("c", "CASH", amount=100), event("l", "CASH", amount=1, quality="LEGACY_SIMULATION")], "2020-01-02")
    with pytest.raises(ValueError, match="ENGINE_MISMATCH"):
        project([event("c", "CASH", amount=100), event("d", "CASH", amount=1, engine_version="other")], "2020-01-02")
