"""Exact expiration payoff math for same-expiration option structures."""
from __future__ import annotations

from collections.abc import Iterable

from .models import OptionLeg, PayoffPoint, PayoffSummary

_EPSILON = 1e-9


def expiration_pnl(legs: Iterable[OptionLeg], underlying_price: float) -> float:
    """Return total dollar P&L at expiration, including entry premiums."""
    if underlying_price < 0:
        raise ValueError("underlying price cannot be negative")
    total = 0.0
    for leg in legs:
        contract = leg.contract
        if contract.right == "C":
            intrinsic = max(underlying_price - contract.strike, 0.0)
        else:
            intrinsic = max(contract.strike - underlying_price, 0.0)
        total += (
            leg.signed_quantity
            * contract.multiplier
            * (intrinsic - leg.entry_price)
        )
    return total


def _unique(values: Iterable[float]) -> list[float]:
    result: list[float] = []
    for value in sorted(values):
        if not result or abs(value - result[-1]) > 1e-7:
            result.append(round(value, 8))
    return result


def summarize_payoff(legs: list[OptionLeg]) -> PayoffSummary:
    """Compute exact piecewise-linear expiration risk metrics.

    A missing max loss means the right tail loses without bound. A missing max
    profit means the right tail profits without bound. The collateral estimate
    is a conservative strategy-level max-loss estimate, not broker margin.
    """
    if not legs:
        raise ValueError("at least one option leg is required")
    symbols = {leg.contract.symbol for leg in legs}
    expirations = {leg.contract.expiration for leg in legs}
    if len(symbols) != 1 or len(expirations) != 1:
        raise ValueError("payoff legs must share a symbol and expiration")

    net_credit = sum(
        -leg.signed_quantity
        * leg.entry_price
        * leg.contract.multiplier
        for leg in legs
    )
    strikes = _unique(leg.contract.strike for leg in legs)
    critical = _unique([0.0, *strikes])
    values = [(price, expiration_pnl(legs, price)) for price in critical]

    right_slope = sum(
        leg.signed_quantity * leg.contract.multiplier
        for leg in legs
        if leg.contract.right == "C"
    )
    finite_max = max(value for _, value in values)
    finite_min = min(value for _, value in values)
    max_profit = None if right_slope > 0 else max(0.0, finite_max)
    max_loss = None if right_slope < 0 else max(0.0, -finite_min)

    roots: list[float] = []
    for (left_x, left_y), (right_x, right_y) in zip(values, values[1:]):
        if abs(left_y) <= _EPSILON:
            roots.append(left_x)
        if left_y * right_y < 0:
            roots.append(
                left_x + (right_x - left_x) * (-left_y) / (right_y - left_y)
            )
    last_x, last_y = values[-1]
    if abs(last_y) <= _EPSILON:
        roots.append(last_x)
    elif right_slope and (root := last_x - last_y / right_slope) >= last_x:
        roots.append(root)

    defined_risk = max_loss is not None
    warnings: list[str] = []
    if not defined_risk:
        warnings.append("expiration loss is unbounded on the upside")
    return_on_risk = None
    if max_loss is not None and max_loss > 0 and max_profit is not None:
        return_on_risk = max_profit / max_loss
    return PayoffSummary(
        net_credit=round(net_credit, 2),
        max_profit=None if max_profit is None else round(max_profit, 2),
        max_loss=None if max_loss is None else round(max_loss, 2),
        collateral_estimate=None if max_loss is None else round(max_loss, 2),
        breakevens=_unique(roots),
        return_on_risk=return_on_risk,
        defined_risk=defined_risk,
        warnings=warnings,
    )


def payoff_points(legs: list[OptionLeg], spot_price: float) -> list[PayoffPoint]:
    """Return exact nodes for plotting a piecewise-linear expiration diagram."""
    if spot_price <= 0:
        raise ValueError("spot price must be positive")
    prices = _unique(
        [0.0, spot_price, *(leg.contract.strike for leg in legs)]
    )
    return [
        PayoffPoint(
            underlying_price=price,
            profit_loss=round(expiration_pnl(legs, price), 2),
        )
        for price in prices
    ]
