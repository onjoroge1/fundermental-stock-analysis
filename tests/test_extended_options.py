from datetime import date, datetime, timezone

import pytest

from stock_machine.market_data.models import (
    MarketDataAvailability,
    MarketQuote,
    OptionChainSnapshot,
    OptionContract,
    OptionQuote,
    UnderlyingContract,
)
from stock_machine.options.extended import covered_call, mixed_expiration

NOW = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)


def _chain(expiration: date, strike: float, right: str = "C", *, bid=4.0, ask=4.2,
           iv=0.25, spot=100.0, conid=1001):
    option = OptionQuote(
        contract=OptionContract(
            provider="fixture", conid=conid, symbol="XYZ", underlying_conid=1,
            expiration=expiration, strike=strike, right=right,
        ),
        quote=MarketQuote(
            provider="fixture", conid=conid, symbol="XYZ", as_of=NOW,
            availability=MarketDataAvailability.REALTIME,
            bid=bid, ask=ask, mark=(bid + ask) / 2,
        ),
        implied_volatility=iv,
        delta=0.5 if right == "C" else -0.5,
        gamma=0.02, theta=-0.05, vega=0.10, open_interest=500,
    )
    return OptionChainSnapshot(
        provider="fixture",
        underlying=UnderlyingContract(
            provider="fixture", symbol="XYZ", conid=1,
            has_options=True, option_months=[expiration.strftime("%b%y").upper()],
        ),
        underlying_quote=MarketQuote(
            provider="fixture", conid=1, symbol="XYZ", as_of=NOW,
            availability=MarketDataAvailability.REALTIME,
            bid=99.9, ask=100.1, mark=spot,
        ),
        month=expiration.strftime("%b%y").upper(),
        options=[option], fetched_at=NOW,
    )


def test_covered_call_exact_expiration_math():
    chain = _chain(date(2026, 9, 18), 105.0, bid=2.0, ask=2.2)
    result = covered_call(chain, 105.0)
    assert result["status"] == "OK"
    assert result["valuation_mode"] == "exact_expiration"
    assert result["max_profit"] == pytest.approx(700.0)
    assert result["max_loss"] == pytest.approx(9800.0)
    assert result["breakeven"] == pytest.approx(98.0)
    assert result["scenario_points"][0]["profit_loss"] == pytest.approx(-9800.0)


def test_call_calendar_uses_far_time_value_at_front_expiry():
    near = _chain(date(2026, 9, 18), 100.0, bid=3.0, ask=3.2, iv=0.22, conid=1001)
    far = _chain(date(2026, 10, 16), 100.0, bid=5.0, ask=5.2, iv=0.25, conid=2001)
    result = mixed_expiration(near, far, 100.0, 100.0, right="C")
    assert result["status"] == "OK"
    assert result["strategy_type"] == "call_calendar"
    assert result["valuation_mode"] == "front_expiry_mark_to_model"
    assert result["exact_max_profit"] is None
    assert result["exact_max_loss"] is None
    assert result["net_debit"] == pytest.approx(220.0)
    assert result["assumptions"]["far_leg_iv_held_constant"] == pytest.approx(0.25)
    assert len(result["scenario_points"]) >= 3


def test_diagonal_is_identified_by_different_strikes():
    near = _chain(date(2026, 9, 18), 100.0, bid=3.0, ask=3.2, conid=1001)
    far = _chain(date(2026, 10, 16), 105.0, bid=3.5, ask=3.8, conid=2001)
    result = mixed_expiration(near, far, 100.0, 105.0, right="C")
    assert result["status"] == "OK"
    assert result["strategy_type"] == "call_diagonal"


def test_mixed_expiration_rejects_missing_iv():
    near = _chain(date(2026, 9, 18), 100.0, bid=3.0, ask=3.2, conid=1001)
    far = _chain(date(2026, 10, 16), 100.0, bid=5.0, ask=5.2, iv=None, conid=2001)
    result = mixed_expiration(near, far, 100.0, 100.0, right="C")
    assert result["status"] == "REJECT"
    assert any("implied volatility" in reason for reason in result["rejection_reasons"])


def test_mixed_expiration_requires_later_far_expiry():
    near = _chain(date(2026, 10, 16), 100.0, conid=1001)
    far = _chain(date(2026, 9, 18), 100.0, conid=2001)
    with pytest.raises(ValueError, match="far expiration"):
        mixed_expiration(near, far, 100.0, 100.0)
