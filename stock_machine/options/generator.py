"""Bounded generation of explainable option strategy candidates."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from ..forecasts.models import ForecastDistribution
from ..market_data.models import (
    MarketDataAvailability,
    OptionChainSnapshot,
    OptionQuote,
)
from .models import (
    LiquidityAssessment,
    OptionAction,
    OptionLeg,
    PositionGreeks,
    RejectedCandidate,
    StrategyCandidate,
    StrategyGenerationResult,
    StrategyType,
)
from .payoff import payoff_points, summarize_payoff
from .ranking import assess_forecast, rank_candidate


class GenerationPolicy(BaseModel):
    """Pre-committed gates for candidate generation."""

    model_config = ConfigDict(extra="forbid")

    min_days_to_expiration: int = Field(default=7, ge=0)
    max_days_to_expiration: int = Field(default=60, ge=1)
    maximum_width: float = Field(default=10.0, gt=0)
    maximum_relative_spread: float = Field(default=0.30, gt=0)
    minimum_open_interest: float = Field(default=50, ge=0)
    minimum_volume: float = Field(default=0, ge=0)
    maximum_quote_age_seconds: float = Field(default=120, ge=0)
    minimum_credit_to_width: float = Field(default=0.10, ge=0, le=1)
    capital_limit: float | None = Field(default=None, gt=0)
    allow_delayed: bool = False
    max_candidates: int = Field(default=50, ge=1, le=250)
    max_combinations: int = Field(default=5000, ge=1, le=50000)
    max_rejections: int = Field(default=100, ge=0, le=1000)
    strategy_types: set[StrategyType] = Field(
        default_factory=lambda: set(StrategyType)
    )


def _spot(chain: OptionChainSnapshot) -> float:
    quote = chain.underlying_quote
    if quote.mark is not None:
        return quote.mark
    if quote.bid is not None and quote.ask is not None:
        return (quote.bid + quote.ask) / 2.0
    if quote.last is not None:
        return quote.last
    raise ValueError("underlying quote has no usable price")


def _entry_leg(option: OptionQuote, action: OptionAction) -> OptionLeg:
    price = option.quote.ask if action == OptionAction.BUY else option.quote.bid
    if price is None or price <= 0:
        raise ValueError(f"{action.value} leg has no positive natural price")
    return OptionLeg(
        contract=option.contract,
        action=action,
        entry_price=price,
        price_basis="natural",
    )


def _liquidity(
    options: list[OptionQuote],
    chain: OptionChainSnapshot,
    policy: GenerationPolicy,
) -> LiquidityAssessment:
    reasons: list[str] = []
    warnings: list[str] = []
    spreads: list[float] = []
    interests: list[float] = []
    volumes: list[float] = []
    availability = sorted(
        {option.quote.availability for option in options}, key=lambda item: item.value
    )
    for option in options:
        quote = option.quote
        if quote.bid is None or quote.ask is None or quote.ask <= 0:
            reasons.append(f"conid {quote.conid} lacks a two-sided quote")
        elif quote.bid > quote.ask:
            reasons.append(f"conid {quote.conid} has a crossed quote")
        else:
            mid = (quote.bid + quote.ask) / 2.0
            relative = (quote.ask - quote.bid) / mid if mid > 0 else float("inf")
            spreads.append(relative)
            if relative > policy.maximum_relative_spread:
                reasons.append(
                    f"conid {quote.conid} relative spread {relative:.1%} exceeds gate"
                )
        age = max(0.0, (chain.fetched_at - quote.as_of).total_seconds())
        if age > policy.maximum_quote_age_seconds:
            reasons.append(f"conid {quote.conid} quote is {age:.0f}s old")
        if quote.availability == MarketDataAvailability.DELAYED:
            if policy.allow_delayed:
                warnings.append(f"conid {quote.conid} uses delayed market data")
            else:
                reasons.append(f"conid {quote.conid} market data is delayed")
        elif quote.availability != MarketDataAvailability.REALTIME:
            reasons.append(
                f"conid {quote.conid} availability is {quote.availability.value}"
            )
        if option.open_interest is None:
            reasons.append(f"conid {quote.conid} lacks open interest")
        else:
            interests.append(option.open_interest)
            if option.open_interest < policy.minimum_open_interest:
                reasons.append(f"conid {quote.conid} open interest is below gate")
        if quote.volume is None:
            warnings.append(f"conid {quote.conid} lacks session volume")
        else:
            volumes.append(quote.volume)
            if quote.volume < policy.minimum_volume:
                reasons.append(f"conid {quote.conid} volume is below gate")

    spread_score = 0.0
    if spreads:
        spread_score = max(
            0.0, 1.0 - max(spreads) / policy.maximum_relative_spread
        )
    oi_score = 0.0
    if interests:
        denominator = max(1.0, policy.minimum_open_interest * 4.0)
        oi_score = min(1.0, min(interests) / denominator)
    volume_score = 0.5 if not volumes else min(1.0, min(volumes) / 100.0)
    score = 0.55 * spread_score + 0.35 * oi_score + 0.10 * volume_score
    return LiquidityAssessment(
        passed=not reasons,
        score=score,
        max_relative_spread=max(spreads) if spreads else None,
        minimum_open_interest=min(interests) if interests else None,
        minimum_volume=min(volumes) if volumes else None,
        availability=availability,
        reasons=list(dict.fromkeys(reasons)),
        warnings=list(dict.fromkeys(warnings)),
    )


def _candidate_id(strategy_type: StrategyType, legs: list[OptionLeg]) -> str:
    identity = "|".join(
        f"{leg.action.value}:{leg.contract.conid}:{leg.quantity}"
        for leg in legs
    )
    digest = sha256(f"{strategy_type.value}|{identity}".encode()).hexdigest()[:12]
    return f"{strategy_type.value}:{digest}"


def _position_greeks(
    option_actions: list[tuple[OptionQuote, OptionAction]],
) -> PositionGreeks:
    fields = {
        "net_delta_shares": "delta",
        "net_gamma": "gamma",
        "net_theta_per_day": "theta",
        "net_vega": "vega",
    }
    values: dict[str, float | None] = {}
    missing: list[str] = []
    for output_name, source_name in fields.items():
        if any(getattr(option, source_name) is None for option, _ in option_actions):
            values[output_name] = None
            missing.append(source_name)
            continue
        values[output_name] = sum(
            (1 if action == OptionAction.BUY else -1)
            * option.contract.multiplier
            * float(getattr(option, source_name))
            for option, action in option_actions
        )
    return PositionGreeks(
        **values,
        complete=not missing,
        warnings=[f"missing provider Greeks: {', '.join(missing)}"] if missing else [],
    )


def _rationale(strategy_type: StrategyType, legs: list[OptionLeg]) -> list[str]:
    natural = ", ".join(
        f"{leg.action.value} {leg.contract.right}{leg.contract.strike:g}"
        for leg in legs
    )
    rationale = [
        f"structure: {natural}",
        "entry uses conservative natural prices (buys at ask, sells at bid)",
        "ranking is heuristic and is not a probability of profit",
    ]
    if strategy_type == StrategyType.CASH_SECURED_PUT:
        strike = legs[0].contract.strike
        effective_price = strike - legs[0].entry_price
        rationale.append(
            f"assignment would acquire shares at an effective ${effective_price:.2f} "
            "before fees"
        )
    return rationale


class _Builder:
    def __init__(
        self,
        chain: OptionChainSnapshot,
        forecast: ForecastDistribution | None,
        policy: GenerationPolicy,
    ) -> None:
        self.chain = chain
        self.forecast = forecast
        self.policy = policy
        self.spot = _spot(chain)
        self.candidates: list[StrategyCandidate] = []
        self.rejected: list[RejectedCandidate] = []
        self.combinations = 0

    def add(
        self,
        strategy_type: StrategyType,
        option_actions: list[tuple[OptionQuote, OptionAction]],
    ) -> StrategyCandidate | None:
        if strategy_type not in self.policy.strategy_types:
            return None
        self.combinations += 1
        if self.combinations > self.policy.max_combinations:
            return None
        options = [item[0] for item in option_actions]
        try:
            legs = [_entry_leg(option, action) for option, action in option_actions]
            payoff = summarize_payoff(legs)
        except ValueError as exc:
            self.reject(strategy_type, options, [str(exc)])
            return None
        if strategy_type == StrategyType.CASH_SECURED_PUT:
            short_put = legs[0]
            payoff.collateral_estimate = round(
                short_put.contract.strike
                * short_put.contract.multiplier
                * short_put.quantity,
                2,
            )
        liquidity = _liquidity(options, self.chain, self.policy)
        reasons = list(liquidity.reasons)
        if not payoff.defined_risk:
            reasons.append("strategy does not have defined expiration risk")
        if (
            self.policy.capital_limit is not None
            and payoff.collateral_estimate is not None
            and payoff.collateral_estimate > self.policy.capital_limit
        ):
            reasons.append("collateral estimate exceeds configured capital limit")
        if strategy_type in {
            StrategyType.BULL_PUT_CREDIT_SPREAD,
            StrategyType.BEAR_CALL_CREDIT_SPREAD,
            StrategyType.IRON_CONDOR,
        }:
            widths = []
            calls = sorted(
                leg.contract.strike for leg in legs if leg.contract.right == "C"
            )
            puts = sorted(
                leg.contract.strike for leg in legs if leg.contract.right == "P"
            )
            if len(calls) == 2:
                widths.append(calls[1] - calls[0])
            if len(puts) == 2:
                widths.append(puts[1] - puts[0])
            width = max(widths, default=0.0)
            credit_per_share = payoff.net_credit / legs[0].contract.multiplier
            if width and credit_per_share / width < self.policy.minimum_credit_to_width:
                reasons.append("credit-to-width ratio is below configured gate")
        if reasons:
            self.reject(strategy_type, options, reasons)
            return None

        expiration = legs[0].contract.expiration
        dte = (expiration - self.chain.fetched_at.date()).days
        forecast = assess_forecast(
            strategy_type,
            payoff,
            dte,
            self.forecast,
            symbol=self.chain.underlying.symbol,
            spot_price=self.spot,
            as_of=self.chain.fetched_at.date(),
        )
        ranking = rank_candidate(payoff, liquidity, forecast)
        position_greeks = _position_greeks(option_actions)
        candidate = StrategyCandidate(
            candidate_id=_candidate_id(strategy_type, legs),
            strategy_type=strategy_type,
            symbol=self.chain.underlying.symbol,
            spot_price=self.spot,
            expiration=expiration,
            days_to_expiration=dte,
            legs=legs,
            payoff=payoff,
            expiration_payoff_points=payoff_points(legs, self.spot),
            position_greeks=position_greeks,
            liquidity=liquidity,
            forecast=forecast,
            ranking=ranking,
            rationale=_rationale(strategy_type, legs),
            warnings=[
                *payoff.warnings,
                *liquidity.warnings,
                *forecast.warnings,
                *position_greeks.warnings,
                "commissions, slippage beyond the natural quote, and taxes are excluded",
                "American-style short options may be assigned early; "
                "expiration can carry pin risk",
            ],
        )
        self.candidates.append(candidate)
        return candidate

    def reject(
        self,
        strategy_type: StrategyType,
        options: list[OptionQuote],
        reasons: list[str],
    ) -> None:
        if len(self.rejected) < self.policy.max_rejections:
            self.rejected.append(
                RejectedCandidate(
                    strategy_type=strategy_type,
                    contracts=[option.contract.conid for option in options],
                    reasons=list(dict.fromkeys(reasons)),
                )
            )


def generate_strategies(
    chain: OptionChainSnapshot,
    forecast: ForecastDistribution | None = None,
    policy: GenerationPolicy | None = None,
) -> StrategyGenerationResult:
    """Generate bounded, same-expiration candidates from a chain snapshot."""
    policy = policy or GenerationPolicy()
    builder = _Builder(chain, forecast, policy)
    chain_warnings = list(chain.warnings)
    chain_warnings.extend(
        [
            "earnings, dividends, and corporate actions are not yet event-screened",
            "IV rank and volatility term structure are not yet available",
        ]
    )
    underlying_availability = chain.underlying_quote.availability
    underlying_age = max(
        0.0,
        (chain.fetched_at - chain.underlying_quote.as_of).total_seconds(),
    )
    if underlying_age > policy.maximum_quote_age_seconds:
        return StrategyGenerationResult(
            symbol=chain.underlying.symbol,
            as_of=chain.fetched_at,
            candidates=[],
            warnings=[f"underlying quote is {underlying_age:.0f}s old"],
            methodology=["no candidates generated from stale underlying data"],
        )
    underlying_quote = chain.underlying_quote
    if (
        underlying_quote.bid is not None
        and underlying_quote.ask is not None
        and underlying_quote.bid > underlying_quote.ask
    ):
        return StrategyGenerationResult(
            symbol=chain.underlying.symbol,
            as_of=chain.fetched_at,
            candidates=[],
            warnings=["underlying quote is crossed"],
            methodology=["no candidates generated from invalid underlying data"],
        )
    if underlying_availability == MarketDataAvailability.DELAYED:
        if policy.allow_delayed:
            chain_warnings.append("underlying quote is delayed")
        else:
            return StrategyGenerationResult(
                symbol=chain.underlying.symbol,
                as_of=chain.fetched_at,
                candidates=[],
                warnings=["underlying market data is delayed"],
                methodology=["no candidates generated from disallowed delayed data"],
            )
    elif underlying_availability != MarketDataAvailability.REALTIME:
        return StrategyGenerationResult(
            symbol=chain.underlying.symbol,
            as_of=chain.fetched_at,
            candidates=[],
            warnings=[
                f"underlying availability is {underlying_availability.value}"
            ],
            methodology=["no candidates generated from unavailable market data"],
        )

    grouped: dict[date, list[OptionQuote]] = defaultdict(list)
    for option in chain.options:
        grouped[option.contract.expiration].append(option)
    vertical_legs: dict[
        tuple[date, StrategyType], list[list[tuple[OptionQuote, OptionAction]]]
    ] = defaultdict(list)

    for expiration, options in sorted(grouped.items()):
        dte = (expiration - chain.fetched_at.date()).days
        if not policy.min_days_to_expiration <= dte <= policy.max_days_to_expiration:
            continue
        puts = sorted(
            [item for item in options if item.contract.right == "P"],
            key=lambda item: item.contract.strike,
        )
        calls = sorted(
            [item for item in options if item.contract.right == "C"],
            key=lambda item: item.contract.strike,
        )

        for put in puts:
            if put.contract.strike <= builder.spot:
                if put.contract.multiplier == 100:
                    builder.add(
                        StrategyType.CASH_SECURED_PUT,
                        [(put, OptionAction.SELL)],
                    )
                else:
                    builder.reject(
                        StrategyType.CASH_SECURED_PUT,
                        [put],
                        [
                            "stock-acquisition candidates require a standard "
                            "100-share multiplier"
                        ],
                    )
        for lower_index, lower in enumerate(puts):
            for higher in puts[lower_index + 1 :]:
                width = higher.contract.strike - lower.contract.strike
                if width > policy.maximum_width:
                    break
                if higher.contract.strike > builder.spot:
                    continue
                bull_put = [(lower, OptionAction.BUY), (higher, OptionAction.SELL)]
                vertical_legs[(expiration, StrategyType.BULL_PUT_CREDIT_SPREAD)].append(
                    bull_put
                )
                builder.add(StrategyType.BULL_PUT_CREDIT_SPREAD, bull_put)
                builder.add(
                    StrategyType.BEAR_PUT_DEBIT_SPREAD,
                    [(lower, OptionAction.SELL), (higher, OptionAction.BUY)],
                )

        for lower_index, lower in enumerate(calls):
            for higher in calls[lower_index + 1 :]:
                width = higher.contract.strike - lower.contract.strike
                if width > policy.maximum_width:
                    break
                if lower.contract.strike < builder.spot:
                    continue
                bear_call = [(lower, OptionAction.SELL), (higher, OptionAction.BUY)]
                vertical_legs[(expiration, StrategyType.BEAR_CALL_CREDIT_SPREAD)].append(
                    bear_call
                )
                builder.add(StrategyType.BEAR_CALL_CREDIT_SPREAD, bear_call)
                builder.add(
                    StrategyType.BULL_CALL_DEBIT_SPREAD,
                    [(lower, OptionAction.BUY), (higher, OptionAction.SELL)],
                )

        put_spreads = vertical_legs[(expiration, StrategyType.BULL_PUT_CREDIT_SPREAD)]
        call_spreads = vertical_legs[(expiration, StrategyType.BEAR_CALL_CREDIT_SPREAD)]
        for put_spread in put_spreads:
            short_put = put_spread[1][0].contract.strike
            for call_spread in call_spreads:
                short_call = call_spread[0][0].contract.strike
                if short_put < short_call:
                    builder.add(
                        StrategyType.IRON_CONDOR,
                        [*put_spread, *call_spread],
                    )
                if builder.combinations >= policy.max_combinations:
                    break
            if builder.combinations >= policy.max_combinations:
                break

    candidates = sorted(
        builder.candidates,
        key=lambda candidate: (
            -candidate.ranking.total,
            candidate.payoff.max_loss or float("inf"),
            candidate.candidate_id,
        ),
    )[: policy.max_candidates]
    if builder.combinations >= policy.max_combinations:
        chain_warnings.append("combination cap reached; search was truncated")
    return StrategyGenerationResult(
        symbol=chain.underlying.symbol,
        as_of=chain.fetched_at,
        candidates=candidates,
        rejected=builder.rejected,
        warnings=list(dict.fromkeys(chain_warnings)),
        methodology=[
            "natural-price entries: buy at ask and sell at bid",
            "hard gates precede heuristic ranking",
            "scores compare candidates; they are not expected return or probability of profit",
            "same-expiration payoff only; calendars and diagonals are excluded",
            "event risk and volatility regime require later data contracts",
            "no order is created or submitted",
        ],
    )
