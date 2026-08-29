"""Extended option structures that require stock overlays or mixed expirations.

The original option engine intentionally assumes one common expiration and an
exact piecewise-linear expiration payoff. This module preserves that invariant
and adds a separate contract for structures that cannot be represented there.

- covered call: exact expiration payoff for 100 shares + one short call;
- calendar/diagonal: front-expiry scenario valuation. The back-month option is
  marked with Black-Scholes using its observed IV and remaining time value.

Calendar/diagonal outputs are scenario estimates, not exact maximum profit or
probability-of-profit claims. No orders are placed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from math import erf, exp, log, sqrt

from ..market_data.models import MarketDataAvailability, OptionChainSnapshot, OptionQuote

MULTIPLIER = 100


class ExtendedStrategyType(StrEnum):
    COVERED_CALL = "covered_call"
    CALL_CALENDAR = "call_calendar"
    PUT_CALENDAR = "put_calendar"
    CALL_DIAGONAL = "call_diagonal"
    PUT_DIAGONAL = "put_diagonal"


@dataclass(frozen=True)
class ExtendedPolicy:
    maximum_relative_spread: float = 0.30
    minimum_open_interest: float = 50.0
    maximum_quote_age_seconds: float = 120.0
    risk_free_rate: float = 0.0
    dividend_yield: float = 0.0
    scenario_low_multiple: float = 0.70
    scenario_high_multiple: float = 1.30
    scenario_points: int = 81


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _bs_price(spot: float, strike: float, years: float, iv: float, right: str,
              rate: float = 0.0, dividend_yield: float = 0.0) -> float:
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if years <= 0:
        return max(spot - strike, 0.0) if right == "C" else max(strike - spot, 0.0)
    if iv <= 0:
        raise ValueError("positive IV required for mixed-expiration valuation")
    root_t = sqrt(years)
    d1 = (log(spot / strike) + (rate - dividend_yield + 0.5 * iv * iv) * years) / (iv * root_t)
    d2 = d1 - iv * root_t
    if right == "C":
        return spot * exp(-dividend_yield * years) * _normal_cdf(d1) - strike * exp(-rate * years) * _normal_cdf(d2)
    return strike * exp(-rate * years) * _normal_cdf(-d2) - spot * exp(-dividend_yield * years) * _normal_cdf(-d1)


def _spot(chain: OptionChainSnapshot) -> float:
    q = chain.underlying_quote
    value = q.mark or ((q.bid + q.ask) / 2.0 if q.bid is not None and q.ask is not None else None) or q.last
    if value is None or value <= 0:
        raise ValueError("underlying quote has no usable price")
    return float(value)


def _option(chain: OptionChainSnapshot, strike: float, right: str) -> OptionQuote:
    matches = [o for o in chain.options if o.contract.right == right and abs(o.contract.strike - strike) < 1e-8]
    if not matches:
        raise ValueError(f"chain has no {strike:g}{right}")
    return matches[0]


def _liquidity(option: OptionQuote, chain: OptionChainSnapshot, policy: ExtendedPolicy) -> tuple[bool, list[str], float]:
    q = option.quote
    reasons: list[str] = []
    if q.availability != MarketDataAvailability.REALTIME:
        reasons.append(f"market data is {q.availability.value}")
    if q.bid is None or q.ask is None or q.ask <= 0 or q.bid < 0 or q.bid > q.ask:
        reasons.append("invalid or missing two-sided quote")
        relative = 1.0
    else:
        mid = (q.bid + q.ask) / 2.0
        relative = (q.ask - q.bid) / mid if mid > 0 else 1.0
        if relative > policy.maximum_relative_spread:
            reasons.append("relative spread exceeds policy")
    age = max(0.0, (chain.fetched_at - q.as_of).total_seconds())
    if age > policy.maximum_quote_age_seconds:
        reasons.append("quote is stale")
    if option.open_interest is None or option.open_interest < policy.minimum_open_interest:
        reasons.append("open interest below policy")
    score = max(0.0, 1.0 - relative / policy.maximum_relative_spread) if policy.maximum_relative_spread else 0.0
    return not reasons, reasons, round(score, 4)


def covered_call(chain: OptionChainSnapshot, call_strike: float,
                 policy: ExtendedPolicy | None = None) -> dict:
    """Exact one-contract covered-call expiration economics."""
    policy = policy or ExtendedPolicy()
    spot = _spot(chain)
    call = _option(chain, call_strike, "C")
    ok, reasons, liquidity_score = _liquidity(call, chain, policy)
    bid = call.quote.bid
    if bid is None or bid <= 0:
        reasons.append("short call lacks positive bid")
        ok = False
        bid = 0.0
    premium = float(bid)
    initial_stock_cost = spot * MULTIPLIER
    max_profit = (call_strike - spot + premium) * MULTIPLIER
    max_loss = (spot - premium) * MULTIPLIER
    breakeven = spot - premium
    points = []
    for s in sorted({0.0, breakeven, spot, call_strike, max(call_strike * 1.25, spot * 1.25)}):
        stock_pnl = (s - spot) * MULTIPLIER
        short_call_pnl = (premium - max(s - call_strike, 0.0)) * MULTIPLIER
        points.append({"underlying_price": round(s, 4), "profit_loss": round(stock_pnl + short_call_pnl, 2)})
    return {
        "status": "OK" if ok else "REJECT",
        "strategy_type": ExtendedStrategyType.COVERED_CALL.value,
        "symbol": chain.underlying.symbol,
        "valuation_mode": "exact_expiration",
        "spot_price": spot,
        "front_expiration": call.contract.expiration.isoformat(),
        "stock_shares": 100,
        "short_option": {"right": "C", "strike": call_strike, "entry_price": premium, "conid": call.contract.conid},
        "initial_stock_cost": round(initial_stock_cost, 2),
        "net_option_credit": round(premium * MULTIPLIER, 2),
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "breakeven": round(breakeven, 4),
        "defined_risk": True,
        "liquidity_score": liquidity_score,
        "rejection_reasons": reasons,
        "scenario_points": points,
        "warnings": ["American-style short call can be assigned early; dividends and taxes are excluded."],
    }


def mixed_expiration(near_chain: OptionChainSnapshot, far_chain: OptionChainSnapshot,
                     near_strike: float, far_strike: float, right: str = "C",
                     policy: ExtendedPolicy | None = None) -> dict:
    """Front-expiry mark-to-model valuation for calendar or diagonal spreads.

    Long far-month option is bought at ask; short near-month option is sold at
    bid. At the near expiration, the short leg is intrinsic and the far leg is
    valued with Black-Scholes using the far option's observed IV.
    """
    policy = policy or ExtendedPolicy()
    if right not in {"C", "P"}:
        raise ValueError("right must be C or P")
    if near_chain.underlying.symbol != far_chain.underlying.symbol:
        raise ValueError("chains must share the same underlying")
    near = _option(near_chain, near_strike, right)
    far = _option(far_chain, far_strike, right)
    if near.contract.expiration >= far.contract.expiration:
        raise ValueError("far expiration must be later than near expiration")
    near_ok, near_reasons, near_liq = _liquidity(near, near_chain, policy)
    far_ok, far_reasons, far_liq = _liquidity(far, far_chain, policy)
    reasons = [f"near: {x}" for x in near_reasons] + [f"far: {x}" for x in far_reasons]
    if near.quote.bid is None or near.quote.bid <= 0:
        reasons.append("near short leg lacks positive bid")
    if far.quote.ask is None or far.quote.ask <= 0:
        reasons.append("far long leg lacks positive ask")
    if far.implied_volatility is None or far.implied_volatility <= 0:
        reasons.append("far long leg lacks positive implied volatility")
    if reasons:
        return {
            "status": "REJECT",
            "strategy_type": (ExtendedStrategyType.CALL_CALENDAR.value if right == "C" else ExtendedStrategyType.PUT_CALENDAR.value) if near_strike == far_strike else (ExtendedStrategyType.CALL_DIAGONAL.value if right == "C" else ExtendedStrategyType.PUT_DIAGONAL.value),
            "symbol": near_chain.underlying.symbol,
            "valuation_mode": "front_expiry_mark_to_model",
            "rejection_reasons": reasons,
        }

    spot = _spot(near_chain)
    debit = float(far.quote.ask) - float(near.quote.bid)
    years_remaining = (far.contract.expiration - near.contract.expiration).days / 365.0
    iv = float(far.implied_volatility)
    strategy = (
        ExtendedStrategyType.CALL_CALENDAR if right == "C" else ExtendedStrategyType.PUT_CALENDAR
    ) if abs(near_strike - far_strike) < 1e-8 else (
        ExtendedStrategyType.CALL_DIAGONAL if right == "C" else ExtendedStrategyType.PUT_DIAGONAL
    )
    lo = max(0.01, spot * policy.scenario_low_multiple)
    hi = spot * policy.scenario_high_multiple
    points = []
    for i in range(max(3, policy.scenario_points)):
        s = lo + (hi - lo) * i / (max(3, policy.scenario_points) - 1)
        far_value = _bs_price(s, far_strike, years_remaining, iv, right,
                              policy.risk_free_rate, policy.dividend_yield)
        near_intrinsic = max(s - near_strike, 0.0) if right == "C" else max(near_strike - s, 0.0)
        pnl = (far_value - near_intrinsic - debit) * MULTIPLIER
        points.append({"underlying_price": round(s, 4), "profit_loss": round(pnl, 2)})
    best = max(points, key=lambda x: x["profit_loss"])
    worst = min(points, key=lambda x: x["profit_loss"])
    return {
        "status": "OK",
        "strategy_type": strategy.value,
        "symbol": near_chain.underlying.symbol,
        "valuation_mode": "front_expiry_mark_to_model",
        "spot_price": spot,
        "near_expiration": near.contract.expiration.isoformat(),
        "far_expiration": far.contract.expiration.isoformat(),
        "near_leg": {"action": "sell", "right": right, "strike": near_strike, "entry_price": float(near.quote.bid), "conid": near.contract.conid},
        "far_leg": {"action": "buy", "right": right, "strike": far_strike, "entry_price": float(far.quote.ask), "conid": far.contract.conid, "implied_volatility": iv},
        "net_debit": round(debit * MULTIPLIER, 2),
        "scenario_best_pnl": best["profit_loss"],
        "scenario_best_underlying": best["underlying_price"],
        "scenario_worst_pnl": worst["profit_loss"],
        "scenario_worst_underlying": worst["underlying_price"],
        "exact_max_profit": None,
        "exact_max_loss": None,
        "defined_risk_at_entry": debit >= 0,
        "liquidity_score": round(min(near_liq, far_liq), 4),
        "scenario_points": points,
        "assumptions": {
            "far_leg_iv_held_constant": iv,
            "risk_free_rate": policy.risk_free_rate,
            "dividend_yield": policy.dividend_yield,
            "remaining_years_at_front_expiry": round(years_remaining, 6),
        },
        "warnings": [
            "Scenario valuation, not exact expiration payoff: the far option retains time value at the front expiration.",
            "Constant-IV Black-Scholes mark is a model assumption; skew, IV crush, dividends, early assignment, financing, commissions and taxes can materially change outcomes.",
        ],
    }
