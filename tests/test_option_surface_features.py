from datetime import date, datetime, timezone

from stock_machine.market_data.models import (
    MarketDataAvailability, MarketQuote, OptionChainSnapshot,
    OptionContract, OptionQuote, UnderlyingContract,
)
from stock_machine.options.surface_features import extract_surface


def _quote(conid, symbol, expiration, strike, right, iv, delta, oi):
    contract = OptionContract(
        provider="test", conid=conid, symbol=symbol, underlying_conid=1,
        expiration=expiration, strike=strike, right=right,
    )
    market = MarketQuote(
        provider="test", conid=conid, symbol=symbol,
        as_of=datetime(2026, 8, 29, 15, tzinfo=timezone.utc),
        availability=MarketDataAvailability.REALTIME, bid=1.0, ask=1.2,
    )
    return OptionQuote(contract=contract, quote=market, implied_volatility=iv,
                       delta=delta, open_interest=oi)


def _chain(expiration, atm_iv):
    symbol = "TEST"
    underlying = UnderlyingContract(provider="test", symbol=symbol, conid=1,
                                    has_options=True)
    uq = MarketQuote(provider="test", conid=1, symbol=symbol,
                     as_of=datetime(2026, 8, 29, 15, tzinfo=timezone.utc),
                     availability=MarketDataAvailability.REALTIME, mark=100.0)
    opts = [
        _quote(10, symbol, expiration, 100, "C", atm_iv, 0.50, 200),
        _quote(11, symbol, expiration, 100, "P", atm_iv + 0.01, -0.50, 250),
        _quote(12, symbol, expiration, 95, "P", atm_iv + 0.06, -0.25, 300),
        _quote(13, symbol, expiration, 105, "C", atm_iv + 0.01, 0.25, 150),
    ]
    return OptionChainSnapshot(provider="test", underlying=underlying,
                               underlying_quote=uq, month="SEP26", options=opts,
                               fetched_at=datetime(2026, 8, 29, 15, tzinfo=timezone.utc))


def test_surface_extracts_skew_term_expected_move_and_percentile():
    near = _chain(date(2026, 9, 30), 0.20)
    far = _chain(date(2026, 12, 31), 0.30)
    history = [{"atm_iv": 0.10 + i * 0.005} for i in range(25)]
    result = extract_surface([near, far], prior_surfaces=history)
    assert result["status"] == "OK"
    f = result["features"]
    assert 0.20 <= f["atm_iv"] <= 0.21
    assert f["iv_skew_25d"] > 0
    assert f["term_slope"] > 0
    assert f["expected_move_pct"] > 0
    assert f["put_call_oi_ratio"] > 1
    assert 0 <= f["iv_percentile"] <= 1
    assert f["has_atm_iv"] == 1.0
    assert f["has_skew"] == 1.0
    assert f["has_term"] == 1.0
    assert f["has_iv_history"] == 1.0


def test_iv_percentile_requires_real_prior_history():
    result = extract_surface([_chain(date(2026, 9, 30), 0.20)],
                             prior_surfaces=[{"atm_iv": 0.18}] * 5)
    assert result["features"]["iv_percentile"] is None
    assert result["features"]["has_iv_history"] == 0.0
