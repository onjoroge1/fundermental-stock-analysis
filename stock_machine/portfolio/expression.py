"""P2-B trade-expression selection.

This module consumes an already-approved P2 portfolio position plus generated
option candidates and decides how to express the exposure. It never submits
orders and never changes portfolio weights.

The selector is deliberately conservative:
- stock is the control expression;
- option candidates must agree with the portfolio direction;
- liquidity/forecast gates must pass;
- structures whose max loss exceeds the position capital budget are rejected;
- unsupported structures remain explicitly unsupported rather than fabricated;
- when no option candidate clearly improves the stock expression, stock wins;
- when the caller requires options and no valid structure exists, NO_TRADE wins.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import fabs

from ..options.models import StrategyCandidate, StrategyType


class ExpressionType(StrEnum):
    STOCK = "stock"
    OPTION = "option"
    NO_TRADE = "no_trade"


@dataclass(frozen=True)
class ExpressionPolicy:
    portfolio_value: float = 100_000.0
    minimum_option_ranking: float = 60.0
    minimum_liquidity_score: float = 0.55
    minimum_forecast_alignment: float = 0.50
    max_option_loss_multiple_of_position_budget: float = 1.0
    option_improvement_margin: float = 5.0
    require_defined_risk: bool = True
    require_options: bool = False
    allow_cash_secured_put: bool = True


LONG_STRATEGIES = {
    StrategyType.CASH_SECURED_PUT,
    StrategyType.BULL_CALL_DEBIT_SPREAD,
    StrategyType.BULL_PUT_CREDIT_SPREAD,
}
SHORT_STRATEGIES = {
    StrategyType.BEAR_PUT_DEBIT_SPREAD,
    StrategyType.BEAR_CALL_CREDIT_SPREAD,
}

UNSUPPORTED_EXPRESSIONS = [
    "covered_call",
    "calendar",
    "diagonal",
]


def _direction(position: dict) -> int:
    weight = float(position.get("weight") or 0.0)
    return 1 if weight > 0 else -1 if weight < 0 else 0


def _stock_score(position: dict) -> float:
    """Transparent control score, not expected return or probability.

    P2-A's proposal score is unbounded and cross-name relative, so normalize
    the interpretable ingredients instead of comparing raw proposal scores to
    the options engine's 0-100 heuristic ranking.
    """
    expected = min(1.0, fabs(float(position.get("expected_excess_return_pct") or 0.0)) / 15.0)
    p = float(position.get("prob_outperform") or 0.5)
    prob_edge = min(1.0, fabs(p - 0.5) / 0.25)
    vol = max(0.01, float(position.get("realized_vol") or 0.30))
    vol_efficiency = min(1.0, 0.30 / vol)
    return round(100.0 * (0.45 * expected + 0.35 * prob_edge + 0.20 * vol_efficiency), 3)


def _candidate_allowed(candidate: StrategyCandidate, direction: int,
                       position_budget: float, policy: ExpressionPolicy) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    allowed_set = LONG_STRATEGIES if direction > 0 else SHORT_STRATEGIES
    if candidate.strategy_type not in allowed_set:
        reasons.append("strategy direction does not match portfolio target")
    if candidate.strategy_type == StrategyType.CASH_SECURED_PUT and not policy.allow_cash_secured_put:
        reasons.append("cash-secured puts disabled by policy")
    if policy.require_defined_risk and not candidate.payoff.defined_risk:
        reasons.append("strategy does not satisfy defined-risk policy")
    if not candidate.liquidity.passed or candidate.liquidity.score < policy.minimum_liquidity_score:
        reasons.append("liquidity gate failed")
    if candidate.ranking.total < policy.minimum_option_ranking:
        reasons.append("option ranking below policy minimum")
    if candidate.forecast.available:
        if candidate.forecast.score < policy.minimum_forecast_alignment:
            reasons.append("forecast alignment below policy minimum")
    else:
        reasons.append("candidate lacks usable forecast assessment")
    max_loss = candidate.payoff.max_loss
    if max_loss is None:
        reasons.append("maximum loss is unavailable")
    elif max_loss > position_budget * policy.max_option_loss_multiple_of_position_budget:
        reasons.append("maximum loss exceeds position capital budget")
    return not reasons, reasons


def _option_score(candidate: StrategyCandidate, position: dict) -> float:
    """Compare candidate quality on a common 0-100 heuristic scale.

    Existing ranking remains the dominant input. A small adjustment rewards
    capped downside and penalizes using a large fraction of the P2 position
    budget. This score is explicitly not expected return.
    """
    budget = max(1.0, fabs(float(position.get("weight") or 0.0)))
    defined_bonus = 4.0 if candidate.payoff.defined_risk else 0.0
    liquidity_bonus = 4.0 * candidate.liquidity.score
    forecast_bonus = 4.0 * candidate.forecast.score
    return round(min(100.0, candidate.ranking.total + defined_bonus + liquidity_bonus + forecast_bonus), 3)


def select_expression(position: dict, candidates: list[StrategyCandidate],
                      policy: ExpressionPolicy | None = None) -> dict:
    policy = policy or ExpressionPolicy()
    direction = _direction(position)
    ticker = position.get("ticker")
    if direction == 0:
        return {
            "status": "NO_TRADE",
            "ticker": ticker,
            "expression": ExpressionType.NO_TRADE.value,
            "reason": "portfolio target weight is zero",
            "unsupported_expressions": UNSUPPORTED_EXPRESSIONS,
        }

    position_budget = fabs(float(position["weight"])) * policy.portfolio_value
    stock_score = _stock_score(position)
    accepted = []
    rejected = []
    for candidate in candidates:
        ok, reasons = _candidate_allowed(candidate, direction, position_budget, policy)
        if not ok:
            rejected.append({
                "candidate_id": candidate.candidate_id,
                "strategy_type": candidate.strategy_type.value,
                "reasons": reasons,
            })
            continue
        accepted.append((candidate, _option_score(candidate, position)))

    accepted.sort(key=lambda item: item[1], reverse=True)
    alternatives = [
        {
            "candidate_id": c.candidate_id,
            "strategy_type": c.strategy_type.value,
            "score": score,
            "ranking": c.ranking.total,
            "max_profit": c.payoff.max_profit,
            "max_loss": c.payoff.max_loss,
            "return_on_risk": c.payoff.return_on_risk,
            "expiration": c.expiration.isoformat(),
            "days_to_expiration": c.days_to_expiration,
        }
        for c, score in accepted[:5]
    ]

    if not accepted:
        if policy.require_options:
            return {
                "status": "NO_TRADE",
                "ticker": ticker,
                "expression": ExpressionType.NO_TRADE.value,
                "stock_control_score": stock_score,
                "position_budget": round(position_budget, 2),
                "reason": "options are required but no candidate passed direction, liquidity, forecast and risk gates",
                "rejected": rejected,
                "unsupported_expressions": UNSUPPORTED_EXPRESSIONS,
            }
        return {
            "status": "OK",
            "ticker": ticker,
            "expression": ExpressionType.STOCK.value,
            "stock_control_score": stock_score,
            "position_budget": round(position_budget, 2),
            "reason": "no option structure cleared the stock control and policy gates",
            "rejected": rejected,
            "unsupported_expressions": UNSUPPORTED_EXPRESSIONS,
        }

    best, option_score = accepted[0]
    if option_score < stock_score + policy.option_improvement_margin:
        return {
            "status": "OK",
            "ticker": ticker,
            "expression": ExpressionType.STOCK.value,
            "stock_control_score": stock_score,
            "best_option_score": option_score,
            "position_budget": round(position_budget, 2),
            "reason": "best option candidate did not beat stock by the configured improvement margin",
            "alternatives": alternatives,
            "rejected": rejected,
            "unsupported_expressions": UNSUPPORTED_EXPRESSIONS,
        }

    return {
        "status": "OK",
        "ticker": ticker,
        "expression": ExpressionType.OPTION.value,
        "stock_control_score": stock_score,
        "best_option_score": option_score,
        "position_budget": round(position_budget, 2),
        "selected": alternatives[0],
        "alternatives": alternatives[1:],
        "reason": "option candidate cleared all policy gates and beat the stock control by the configured margin",
        "unsupported_expressions": UNSUPPORTED_EXPRESSIONS,
        "methodology": "heuristic expression comparison; not expected return, probability of profit, or an order recommendation",
    }
