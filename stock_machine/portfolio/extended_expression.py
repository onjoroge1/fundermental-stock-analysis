"""Bridge extended option structures into P2 trade-expression review.

Covered calls have exact expiration max-loss math and can therefore compete
with the stock control today. Mixed-expiration structures remain analysis-only
until P2 has a path/assignment-aware capital-at-risk contract; their scenario
worst case is deliberately not treated as exact max loss.
"""
from __future__ import annotations

from math import fabs

from .expression import ExpressionPolicy, _stock_score


def compare_extended(position: dict, extended_candidates: list[dict],
                     policy: ExpressionPolicy | None = None) -> dict:
    policy = policy or ExpressionPolicy()
    ticker = position.get("ticker")
    weight = float(position.get("weight") or 0.0)
    direction = 1 if weight > 0 else -1 if weight < 0 else 0
    if direction == 0:
        return {"status": "NO_TRADE", "ticker": ticker,
                "reason": "portfolio target weight is zero"}

    budget = fabs(weight) * policy.portfolio_value
    stock_score = _stock_score(position)
    accepted = []
    analysis_only = []
    rejected = []

    for candidate in extended_candidates:
        strategy = candidate.get("strategy_type")
        if candidate.get("status") != "OK":
            rejected.append({"strategy_type": strategy,
                             "reasons": candidate.get("rejection_reasons") or ["extended valuation rejected"]})
            continue
        if strategy in {"call_calendar", "put_calendar", "call_diagonal", "put_diagonal"}:
            analysis_only.append({
                "strategy_type": strategy,
                "valuation_mode": candidate.get("valuation_mode"),
                "scenario_best_pnl": candidate.get("scenario_best_pnl"),
                "scenario_worst_pnl": candidate.get("scenario_worst_pnl"),
                "reason": "mixed-expiration scenario loss is not an exact capital-at-risk bound",
            })
            continue
        if strategy != "covered_call":
            rejected.append({"strategy_type": strategy, "reasons": ["unsupported extended strategy"]})
            continue
        if direction < 0:
            rejected.append({"strategy_type": strategy,
                             "reasons": ["covered call requires a long stock target"]})
            continue
        max_loss = candidate.get("max_loss")
        if max_loss is None or float(max_loss) > budget * policy.max_option_loss_multiple_of_position_budget:
            rejected.append({"strategy_type": strategy,
                             "reasons": ["maximum loss exceeds position capital budget"]})
            continue
        liq = float(candidate.get("liquidity_score") or 0.0)
        if liq < policy.minimum_liquidity_score:
            rejected.append({"strategy_type": strategy, "reasons": ["liquidity gate failed"]})
            continue

        # Covered-call score rewards liquid premium income while penalizing the
        # fraction of stock upside capped by the short strike. This is a
        # comparison heuristic, never expected return or probability of profit.
        spot = float(candidate.get("spot_price") or 0.0)
        strike = float((candidate.get("short_option") or {}).get("strike") or spot)
        credit = float(candidate.get("net_option_credit") or 0.0)
        premium_yield = min(1.0, credit / max(1.0, spot * 100.0) / 0.05)
        upside_room = min(1.0, max(0.0, strike / max(spot, 1e-9) - 1.0) / 0.15)
        score = round(100.0 * (0.45 * liq + 0.30 * premium_yield + 0.25 * upside_room), 3)
        accepted.append({"candidate": candidate, "score": score})

    accepted.sort(key=lambda x: x["score"], reverse=True)
    if not accepted:
        return {
            "status": "OK",
            "ticker": ticker,
            "expression": "stock",
            "stock_control_score": stock_score,
            "reason": "no extended structure cleared exact-risk and liquidity gates",
            "analysis_only": analysis_only,
            "rejected": rejected,
        }

    best = accepted[0]
    if best["score"] < stock_score + policy.option_improvement_margin:
        return {
            "status": "OK", "ticker": ticker, "expression": "stock",
            "stock_control_score": stock_score,
            "best_extended_score": best["score"],
            "reason": "best extended candidate did not beat stock by the configured margin",
            "analysis_only": analysis_only, "rejected": rejected,
        }

    c = best["candidate"]
    return {
        "status": "OK", "ticker": ticker, "expression": "option_overlay",
        "stock_control_score": stock_score,
        "best_extended_score": best["score"],
        "selected": {
            "strategy_type": c["strategy_type"],
            "front_expiration": c.get("front_expiration"),
            "max_profit": c.get("max_profit"),
            "max_loss": c.get("max_loss"),
            "breakeven": c.get("breakeven"),
        },
        "reason": "covered call has exact risk math, cleared liquidity/capital gates, and beat stock control",
        "analysis_only": analysis_only,
        "rejected": rejected,
        "methodology": "heuristic expression comparison; not expected return, probability of profit, or order advice",
    }
