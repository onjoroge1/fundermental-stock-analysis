from datetime import date, datetime, timezone

from stock_machine.forecasts.models import ForecastDistribution, ForecastHorizon, PriceQuantiles, ReturnQuantiles
from stock_machine.market_data.models import MarketDataAvailability, MarketQuote, OptionChainSnapshot, OptionContract, OptionQuote, UnderlyingContract
from stock_machine.options import GenerationPolicy, StrategyType, generate_strategies
from stock_machine.portfolio.expression import ExpressionPolicy, select_expression

NOW = datetime(2026, 8, 18, 14, 30, tzinfo=timezone.utc)
EXP = date(2026, 9, 18)


def _contract(conid, strike, right):
    return OptionContract(provider="fixture", conid=conid, symbol="TEST",
                          underlying_conid=1, expiration=EXP, strike=strike, right=right)


def _option(conid, strike, right, bid, ask):
    return OptionQuote(
        contract=_contract(conid, strike, right),
        quote=MarketQuote(provider="fixture", conid=conid, as_of=NOW,
                          availability=MarketDataAvailability.REALTIME,
                          bid=bid, ask=ask, mark=(bid + ask) / 2, volume=500),
        implied_volatility=0.30,
        delta=-0.25 if right == "P" else 0.25,
        gamma=0.02, theta=-0.08, vega=0.15, open_interest=1500,
    )


def _chain():
    options = [
        _option(1, 90, "P", 1.0, 1.1),
        _option(2, 95, "P", 2.0, 2.1),
        _option(3, 100, "P", 4.0, 4.1),
        _option(4, 100, "C", 4.0, 4.1),
        _option(5, 105, "C", 2.0, 2.1),
        _option(6, 110, "C", 1.0, 1.1),
    ]
    return OptionChainSnapshot(
        provider="fixture",
        underlying=UnderlyingContract(provider="fixture", symbol="TEST", conid=1,
                                      has_options=True, option_months=["SEP26"]),
        underlying_quote=MarketQuote(provider="fixture", conid=1, symbol="TEST",
                                     as_of=NOW, availability=MarketDataAvailability.REALTIME,
                                     bid=99.9, ask=100.1, mark=100),
        month="SEP26", options=options, fetched_at=NOW,
    )


def _forecast(probability_up=0.65):
    return ForecastDistribution(
        symbol="TEST", as_of=NOW.date(), spot_price=100, source="fixture",
        primary_model="fixture",
        horizons=[ForecastHorizon(
            horizon_days=30, probability_up=probability_up,
            expected_return=0.08 if probability_up > 0.5 else -0.08,
            expected_price=108 if probability_up > 0.5 else 92,
            expected_return_method="median",
            price_quantiles=PriceQuantiles(p10=85, p50=108 if probability_up > 0.5 else 92, p90=115),
            return_quantiles=ReturnQuantiles(p10=-0.15, p50=0.08 if probability_up > 0.5 else -0.08, p90=0.15),
            calibration_status="calibrated", baseline_status="beats_baseline",
            model_name="fixture",
        )],
    )


def _position(weight=0.08, expected=8.0, prob=0.65):
    return {"ticker": "TEST", "weight": weight,
            "expected_excess_return_pct": expected,
            "prob_outperform": prob, "realized_vol": 0.30}


def test_long_position_rejects_bearish_and_neutral_structures():
    generated = generate_strategies(
        _chain(), _forecast(0.65),
        GenerationPolicy(capital_limit=8000, maximum_relative_spread=0.50,
                         minimum_open_interest=50, max_candidates=100),
    )
    result = select_expression(
        _position(), generated.candidates,
        ExpressionPolicy(portfolio_value=100000, minimum_option_ranking=0,
                         minimum_liquidity_score=0, minimum_forecast_alignment=0,
                         option_improvement_margin=-100),
    )
    if result["expression"] == "option":
        assert result["selected"]["strategy_type"] in {
            StrategyType.BULL_CALL_DEBIT_SPREAD.value,
            StrategyType.BULL_PUT_CREDIT_SPREAD.value,
            StrategyType.CASH_SECURED_PUT.value,
        }
    rejected_types = {r["strategy_type"] for r in result.get("rejected", [])}
    assert StrategyType.IRON_CONDOR.value in rejected_types or StrategyType.BEAR_CALL_CREDIT_SPREAD.value in rejected_types


def test_stock_remains_control_when_options_do_not_improve_enough():
    result = select_expression(
        _position(), [], ExpressionPolicy(require_options=False)
    )
    assert result["status"] == "OK"
    assert result["expression"] == "stock"
    assert "unsupported_expressions" in result


def test_require_options_fails_closed_when_no_candidate_passes():
    result = select_expression(
        _position(), [], ExpressionPolicy(require_options=True)
    )
    assert result["status"] == "NO_TRADE"
    assert result["expression"] == "no_trade"


def test_short_target_never_selects_bullish_structure():
    generated = generate_strategies(
        _chain(), _forecast(0.35),
        GenerationPolicy(capital_limit=8000, maximum_relative_spread=0.50,
                         minimum_open_interest=50, max_candidates=100),
    )
    result = select_expression(
        _position(weight=-0.08, expected=-8.0, prob=0.35), generated.candidates,
        ExpressionPolicy(portfolio_value=100000, minimum_option_ranking=0,
                         minimum_liquidity_score=0, minimum_forecast_alignment=0,
                         option_improvement_margin=-100),
    )
    if result["expression"] == "option":
        assert result["selected"]["strategy_type"] in {
            StrategyType.BEAR_PUT_DEBIT_SPREAD.value,
            StrategyType.BEAR_CALL_CREDIT_SPREAD.value,
        }
