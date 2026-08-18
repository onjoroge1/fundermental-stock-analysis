import json
from datetime import date, datetime, timezone

import pytest

from stock_machine.forecasts.models import (
    ForecastDistribution,
    ForecastHorizon,
    PriceQuantiles,
    ReturnQuantiles,
)
from stock_machine.market_data.models import (
    MarketDataAvailability,
    MarketQuote,
    OptionChainSnapshot,
    OptionContract,
    OptionQuote,
    UnderlyingContract,
)
from stock_machine.options import (
    GenerationPolicy,
    OptionAction,
    OptionLeg,
    StrategyType,
    expiration_pnl,
    generate_strategies,
    load_latest_forecast,
    payoff_points,
    summarize_payoff,
)

NOW = datetime(2026, 8, 18, 14, 30, tzinfo=timezone.utc)
EXPIRATION = date(2026, 9, 18)


def contract(conid: int, strike: float, right: str) -> OptionContract:
    return OptionContract(
        provider="fixture",
        conid=conid,
        symbol="SPY",
        underlying_conid=756733,
        expiration=EXPIRATION,
        strike=strike,
        right=right,
    )


def option(
    conid: int,
    strike: float,
    right: str,
    bid: float,
    ask: float,
    *,
    availability: MarketDataAvailability = MarketDataAvailability.REALTIME,
    open_interest: float | None = 1000,
    volume: float | None = 200,
) -> OptionQuote:
    return OptionQuote(
        contract=contract(conid, strike, right),
        quote=MarketQuote(
            provider="fixture",
            conid=conid,
            as_of=NOW,
            availability=availability,
            bid=bid,
            ask=ask,
            mark=(bid + ask) / 2,
            volume=volume,
        ),
        implied_volatility=0.18,
        delta=-0.25 if right == "P" else 0.25,
        gamma=0.02,
        theta=-0.08,
        vega=0.15,
        open_interest=open_interest,
    )


def chain(
    *, availability: MarketDataAvailability = MarketDataAvailability.REALTIME
) -> OptionChainSnapshot:
    options = [
        option(1001, 630, "P", 1.30, 1.50, availability=availability),
        option(1002, 635, "P", 2.00, 2.20, availability=availability),
        option(1003, 640, "P", 3.00, 3.20, availability=availability),
        option(2001, 660, "C", 2.80, 3.00, availability=availability),
        option(2002, 665, "C", 1.90, 2.10, availability=availability),
        option(2003, 670, "C", 1.20, 1.40, availability=availability),
    ]
    return OptionChainSnapshot(
        provider="fixture",
        underlying=UnderlyingContract(
            provider="fixture",
            symbol="SPY",
            conid=756733,
            has_options=True,
            option_months=["SEP26"],
        ),
        underlying_quote=MarketQuote(
            provider="fixture",
            conid=756733,
            symbol="SPY",
            as_of=NOW,
            availability=availability,
            bid=649.95,
            ask=650.05,
            mark=650,
        ),
        month="SEP26",
        options=options,
        fetched_at=NOW,
    )


def forecast(*, calibrated: bool = True) -> ForecastDistribution:
    return ForecastDistribution(
        symbol="SPY",
        as_of=NOW.date(),
        spot_price=650,
        source="fixture",
        primary_model="fixture",
        horizons=[
            ForecastHorizon(
                horizon_days=30,
                probability_up=0.55,
                expected_return=0.01,
                expected_price=656.5,
                expected_return_method="median",
                price_quantiles=PriceQuantiles(p10=625, p50=656.5, p90=680),
                return_quantiles=ReturnQuantiles(
                    p10=625 / 650 - 1,
                    p50=0.01,
                    p90=680 / 650 - 1,
                ),
                calibration_status="calibrated" if calibrated else "pending",
                baseline_status="beats_baseline",
                model_name="fixture",
            )
        ],
    )


def leg(strike: float, right: str, action: OptionAction, price: float) -> OptionLeg:
    return OptionLeg(
        contract=contract(int(strike * 10) + (0 if right == "P" else 1), strike, right),
        action=action,
        entry_price=price,
        price_basis="manual",
    )


def test_bull_put_spread_has_exact_expiration_metrics():
    legs = [
        leg(635, "P", OptionAction.BUY, 2.20),
        leg(640, "P", OptionAction.SELL, 3.00),
    ]
    summary = summarize_payoff(legs)
    assert summary.net_credit == 80
    assert summary.max_profit == 80
    assert summary.max_loss == 420
    assert summary.breakevens == [639.2]
    assert expiration_pnl(legs, 650) == pytest.approx(80)
    assert expiration_pnl(legs, 630) == pytest.approx(-420)
    points = payoff_points(legs, 650)
    assert [(point.underlying_price, point.profit_loss) for point in points] == [
        (0.0, -420.0),
        (635.0, -420.0),
        (640.0, 80.0),
        (650.0, 80.0),
    ]


def test_cash_secured_put_has_finite_stock_acquisition_risk():
    summary = summarize_payoff([leg(640, "P", OptionAction.SELL, 3.00)])
    assert summary.defined_risk is True
    assert summary.net_credit == 300
    assert summary.max_profit == 300
    assert summary.max_loss == 63700
    assert summary.collateral_estimate == 63700
    assert summary.breakevens == [637]


def test_naked_short_call_is_flagged_as_unbounded():
    summary = summarize_payoff([leg(660, "C", OptionAction.SELL, 2.50)])
    assert summary.defined_risk is False
    assert summary.max_loss is None
    assert "unbounded" in summary.warnings[0]


def test_generator_produces_only_defined_risk_natural_price_candidates():
    result = generate_strategies(
        chain(),
        forecast(),
        GenerationPolicy(capital_limit=1000, max_candidates=100),
    )
    types = {candidate.strategy_type for candidate in result.candidates}
    assert StrategyType.BULL_PUT_CREDIT_SPREAD in types
    assert StrategyType.BEAR_CALL_CREDIT_SPREAD in types
    assert StrategyType.IRON_CONDOR in types
    assert StrategyType.CASH_SECURED_PUT not in types
    assert all(candidate.payoff.defined_risk for candidate in result.candidates)
    assert all(
        leg.price_basis == "natural"
        for candidate in result.candidates
        for leg in candidate.legs
    )
    assert all(
        len(candidate.expiration_payoff_points) >= 2
        for candidate in result.candidates
    )
    assert all(candidate.position_greeks.complete for candidate in result.candidates)
    assert [candidate.ranking.total for candidate in result.candidates] == sorted(
        [candidate.ranking.total for candidate in result.candidates], reverse=True
    )


def test_generated_cash_secured_put_reserves_full_strike_cash():
    result = generate_strategies(
        chain(),
        policy=GenerationPolicy(
            strategy_types={StrategyType.CASH_SECURED_PUT},
            capital_limit=65000,
        ),
    )
    candidate = next(
        item
        for item in result.candidates
        if item.legs[0].contract.strike == 640
    )
    assert candidate.payoff.max_loss == 63700
    assert candidate.payoff.collateral_estimate == 64000
    assert any("effective $637.00" in text for text in candidate.rationale)


def test_generator_keeps_calibration_distinct_from_alignment():
    result = generate_strategies(
        chain(),
        forecast(calibrated=False),
        GenerationPolicy(capital_limit=1000, max_candidates=10),
    )
    assert result.candidates
    assessment = result.candidates[0].forecast
    assert assessment.calibration_status == "pending"
    assert any("not calibrated" in warning for warning in assessment.warnings)
    assert "not expected return" in result.candidates[0].ranking.methodology


def test_future_dated_forecast_is_not_allowed_to_influence_ranking():
    future = forecast()
    future.as_of = date(2026, 8, 19)
    result = generate_strategies(
        chain(),
        future,
        GenerationPolicy(capital_limit=1000, max_candidates=5),
    )
    assert result.candidates
    assessment = result.candidates[0].forecast
    assert assessment.available is False
    assert assessment.score == 0.5
    assert "after" in assessment.warnings[0]


def test_delayed_data_is_rejected_unless_policy_explicitly_allows_it():
    rejected = generate_strategies(chain(availability=MarketDataAvailability.DELAYED))
    assert rejected.candidates == []
    assert "delayed" in rejected.warnings[0]

    allowed = generate_strategies(
        chain(availability=MarketDataAvailability.DELAYED),
        policy=GenerationPolicy(allow_delayed=True, capital_limit=1000),
    )
    assert allowed.candidates
    assert any("delayed" in warning for warning in allowed.candidates[0].warnings)


def test_liquidity_gate_rejects_wide_or_missing_quotes():
    poor = chain()
    poor.options[1].quote.ask = 4.0
    poor.options[2].open_interest = None
    result = generate_strategies(
        poor,
        policy=GenerationPolicy(capital_limit=1000, max_rejections=100),
    )
    assert result.rejected
    reasons = [reason for item in result.rejected for reason in item.reasons]
    assert any("spread" in reason for reason in reasons)
    assert any("open interest" in reason for reason in reasons)


def test_stale_underlying_blocks_all_candidates():
    stale = chain()
    stale.underlying_quote.as_of = datetime(
        2026, 8, 18, 14, 0, tzinfo=timezone.utc
    )
    result = generate_strategies(stale)
    assert result.candidates == []
    assert "old" in result.warnings[0]


def test_stock_acquisition_rejects_nonstandard_contract_multiplier():
    adjusted = chain()
    for item in adjusted.options:
        item.contract.multiplier = 10
    result = generate_strategies(
        adjusted,
        policy=GenerationPolicy(
            strategy_types={StrategyType.CASH_SECURED_PUT},
            max_rejections=20,
        ),
    )
    assert result.candidates == []
    assert all(
        "100-share" in reason
        for rejection in result.rejected
        for reason in rejection.reasons
    )


def test_search_and_output_are_bounded():
    result = generate_strategies(
        chain(),
        forecast(),
        GenerationPolicy(
            capital_limit=1000,
            max_candidates=3,
            max_combinations=8,
            max_rejections=2,
        ),
    )
    assert len(result.candidates) <= 3
    assert len(result.rejected) <= 2
    assert "combination cap" in result.warnings[-1]


def test_latest_forecast_loader_skips_invalid_newer_cache(tmp_path):
    valid = forecast().model_dump(mode="json")
    (tmp_path / "SPY_2026-08-17.json").write_text(
        json.dumps({"forecast_distribution": valid}), encoding="utf-8"
    )
    (tmp_path / "SPY_2026-08-18.json").write_text("not json", encoding="utf-8")
    loaded = load_latest_forecast("spy", tmp_path)
    assert loaded is not None
    assert loaded.symbol == "SPY"
    assert loaded.as_of == date(2026, 8, 18)
