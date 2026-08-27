"""Simulator tests: strategy shapes and payoff math against hand-computed
values. No broker connection required — chains are constructed in-memory."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from stock_machine.market_data.models import (
    MarketDataAvailability, MarketQuote, OptionChainSnapshot, OptionContract,
    OptionQuote, UnderlyingContract,
)
from stock_machine.options.simulator import (
    StrategyBuildError, TEMPLATES, build_legs, list_templates, simulate,
)

SYM, CONID, EXP = "TEST", 1000, date(2026, 9, 25)


def _opt(strike: float, right: str, bid: float, ask: float) -> OptionQuote:
    contract = OptionContract(
        provider="test", conid=CONID + int(strike) * 10 + (1 if right == "C" else 2),
        symbol=SYM, underlying_conid=CONID, expiration=EXP, strike=strike,
        right=right,
    )
    return OptionQuote(
        contract=contract,
        quote=MarketQuote(
            provider="test", conid=contract.conid, symbol=SYM,
            as_of=datetime.now(timezone.utc),
            availability=MarketDataAvailability.REALTIME,
            bid=bid, ask=ask, mark=(bid + ask) / 2,
        ),
    )


def _chain() -> OptionChainSnapshot:
    underlying = UnderlyingContract(provider="test", symbol=SYM, conid=CONID)
    quote = MarketQuote(
        provider="test", conid=CONID, symbol=SYM,
        as_of=datetime.now(timezone.utc),
        availability=MarketDataAvailability.REALTIME,
        bid=99.9, ask=100.1, mark=100.0, last=100.0,
    )
    options = []
    for strike, cb, ca, pb, pa in (
        (90.0, 12.0, 12.4, 2.0, 2.2),
        (100.0, 6.0, 6.4, 6.0, 6.4),
        (110.0, 2.0, 2.4, 12.0, 12.4),
    ):
        options.append(_opt(strike, "C", cb, ca))
        options.append(_opt(strike, "P", pb, pa))
    return OptionChainSnapshot(
        provider="test", underlying=underlying, underlying_quote=quote,
        month="SEP26", options=options,
    )


def test_jade_lizard_leg_shape():
    """Short put + short call + long higher call, all same expiration."""
    legs, _ = build_legs(_chain(), "jade_lizard", [90.0, 100.0, 110.0])
    assert len(legs) == 3
    shape = {(l.contract.strike, l.contract.right, l.action.value) for l in legs}
    assert shape == {(90.0, "P", "sell"), (100.0, "C", "sell"), (110.0, "C", "buy")}
    assert len({l.contract.expiration for l in legs}) == 1


def test_jade_lizard_conservative_fills():
    """Sells fill at the bid, buys at the ask — never the favourable side."""
    legs, notes = build_legs(_chain(), "jade_lizard", [90.0, 100.0, 110.0])
    by = {(l.contract.strike, l.contract.right): l for l in legs}
    assert by[(90.0, "P")].entry_price == 2.0     # sold put -> bid
    assert by[(100.0, "C")].entry_price == 6.0    # sold call -> bid
    assert by[(110.0, "C")].entry_price == 2.4    # bought call -> ask
    assert all(l.price_basis == "natural" for l in legs) and notes == []


def test_jade_lizard_payoff_matches_hand_calculation():
    r = simulate(_chain(), "jade_lizard", [90.0, 100.0, 110.0])
    # credit = 2.00 + 6.00 - 2.40 = 5.60 -> $560 on one contract
    assert r["summary"]["net_credit"] == pytest.approx(560.0)
    # credit 5.60 < call width 10 -> upside risk remains
    assert any("upside risk is NOT eliminated" in w for w in r["warnings"])
    # at expiry with spot at 100: put expires worthless, short call at the money,
    # long call worthless -> keep the full credit
    assert r["pnl_at_spot"] == pytest.approx(560.0)


def test_true_jade_lizard_has_no_upside_risk():
    """When credit >= call width the structure is upside-risk-free; the
    simulator must confirm rather than assume it."""
    chain = _chain()
    fat = [o for o in chain.options
           if o.contract.right == "C" and o.contract.strike == 100.0][0]
    fat.quote.bid, fat.quote.ask = 13.0, 13.4   # rich short call
    r = simulate(chain, "jade_lizard", [90.0, 100.0, 110.0])
    assert any("no upside risk at expiration" in w for w in r["warnings"])
    # far above the long call the P&L must not be negative
    far = max(p["profit_loss"] for p in r["payoff"]
              if p["underlying_price"] >= 150)
    assert far >= 0


def test_vertical_spread_max_loss_is_width_minus_credit():
    r = simulate(_chain(), "bull_put_credit_spread", [90.0, 100.0])
    # sell 100P at 6.00, buy 90P at 2.20 -> credit 3.80; width 10
    assert r["summary"]["net_credit"] == pytest.approx(380.0)
    assert r["summary"]["max_loss"] == pytest.approx(620.0)
    assert r["summary"]["defined_risk"] is True


def test_strike_count_is_enforced():
    with pytest.raises(StrategyBuildError, match="needs 3 strike"):
        build_legs(_chain(), "jade_lizard", [90.0, 100.0])


def test_missing_strike_is_reported_not_guessed():
    with pytest.raises(StrategyBuildError, match="no 95"):
        build_legs(_chain(), "bull_put_credit_spread", [95.0, 100.0])


def test_unknown_strategy_lists_alternatives():
    with pytest.raises(StrategyBuildError, match="available:"):
        build_legs(_chain(), "not_a_strategy", [100.0])


def test_every_template_builds_and_is_self_consistent():
    chain = _chain()
    ladder = [90.0, 100.0, 110.0]
    for key, tpl in TEMPLATES.items():
        if tpl.strikes_required > len(ladder):
            continue
        strikes = ladder[: tpl.strikes_required]
        r = simulate(chain, key, strikes)
        assert r["strategy"]["key"] == key
        assert len(r["legs"]) == len(tpl.legs)
        assert r["payoff"] and r["disclaimer"]


def test_undefined_risk_structures_report_unbounded_loss():
    """A short strangle must not claim a finite max loss."""
    r = simulate(_chain(), "short_strangle", [90.0, 110.0])
    assert r["summary"]["max_loss"] is None
    assert r["summary"]["defined_risk"] is False
    assert "undefined_risk" in r["strategy"]["tags"]


def test_list_templates_exposes_jade_lizard():
    keys = {t["key"] for t in list_templates()}
    assert "jade_lizard" in keys and "iron_condor" in keys
