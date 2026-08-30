"""Bridge extended option structures into P2 trade-expression review.

Covered calls use exact expiration max-loss math. Calendars and diagonals may
enter automated comparison only after the P2-D path-risk contract clears a
conservative economic-loss bound, transient assignment-notional gate, event
screen, and liquidity requirements.

P2-E adds a candidate-specific event-screen factory so each calendar/diagonal
is screened against its own front/far expirations rather than one generic
strategy-level calendar.

This module remains review-only: it never places an order or changes a P2-A
portfolio target weight.
"""
from __future__ import annotations

from collections.abc import Callable
from math import fabs

from ..options.path_risk import PathRiskPolicy, assess_mixed_path_risk
from .expression import ExpressionPolicy, _stock_score

MIXED_LONG = {"call_calendar", "call_diagonal"}
MIXED_SHORT = {"put_calendar", "put_diagonal"}
MIXED_ALL = MIXED_LONG | MIXED_SHORT


def _mixed_score(candidate: dict, risk: dict, direction: int,
                 budget: float, path_policy: PathRiskPolicy) -> float:
    """Transparent 0-100 comparison heuristic; never expected return."""
    liq = float(candidate.get("liquidity_score") or 0.0)
    economic = float(risk.get("conservative_economic_max_loss") or budget)
    assignment_multiple = float(risk.get("assignment_notional_multiple_of_budget") or 99.0)
    economic_efficiency = max(0.0, 1.0 - economic / max(budget, 1.0))
    assignment_efficiency = max(
        0.0,
        1.0 - assignment_multiple / max(path_policy.max_assignment_notional_multiple_of_budget, 1e-9),
    )
    spot = float(candidate.get("spot_price") or 0.0)
    best_underlying = float(candidate.get("scenario_best_underlying") or spot)
    scenario_alignment = 1.0 if (
        (direction > 0 and best_underlying >= spot)
        or (direction < 0 and best_underlying <= spot)
    ) else 0.0
    return round(100.0 * (
        0.40 * liq
        + 0.30 * economic_efficiency
        + 0.15 * assignment_efficiency
        + 0.15 * scenario_alignment
    ), 3)


def compare_extended(position: dict, extended_candidates: list[dict],
                     policy: ExpressionPolicy | None = None,
                     *, event_screens: dict[str, dict] | None = None,
                     event_screen_factory: Callable[[dict], dict] | None = None,
                     path_policy: PathRiskPolicy | None = None) -> dict:
    policy = policy or ExpressionPolicy()
    path_policy = path_policy or PathRiskPolicy()
    event_screens = event_screens or {}
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

        if strategy in MIXED_ALL:
            if (direction > 0 and strategy not in MIXED_LONG) or (
                direction < 0 and strategy not in MIXED_SHORT
            ):
                rejected.append({
                    "strategy_type": strategy,
                    "reasons": ["mixed-expiration structure direction does not match portfolio target"],
                })
                continue
            if event_screen_factory is not None:
                event_screen = event_screen_factory(candidate)
            else:
                event_screen = event_screens.get(strategy)
            risk = assess_mixed_path_risk(
                candidate,
                budget,
                event_screen=event_screen,
                policy=path_policy,
            )
            if not risk["automation_eligible"]:
                analysis_only.append({
                    "strategy_type": strategy,
                    "valuation_mode": candidate.get("valuation_mode"),
                    "scenario_best_pnl": candidate.get("scenario_best_pnl"),
                    "scenario_worst_pnl": candidate.get("scenario_worst_pnl"),
                    "event_screen": event_screen,
                    "path_risk": risk,
                    "reason": "mixed-expiration structure remains analysis-only because a path-risk gate failed",
                })
                continue
            accepted.append({
                "candidate": candidate,
                "score": _mixed_score(candidate, risk, direction, budget, path_policy),
                "event_screen": event_screen,
                "path_risk": risk,
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
        accepted.append({"candidate": candidate, "score": score,
                         "event_screen": None, "path_risk": None})

    accepted.sort(key=lambda x: x["score"], reverse=True)
    if not accepted:
        return {
            "status": "OK",
            "ticker": ticker,
            "expression": "stock",
            "stock_control_score": stock_score,
            "reason": "no extended structure cleared direction, risk, liquidity and event gates",
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
    selected = {
        "strategy_type": c["strategy_type"],
        "front_expiration": c.get("front_expiration") or c.get("near_expiration"),
        "far_expiration": c.get("far_expiration"),
        "max_profit": c.get("max_profit"),
        "max_loss": c.get("max_loss"),
        "breakeven": c.get("breakeven"),
        "scenario_best_pnl": c.get("scenario_best_pnl"),
        "scenario_worst_pnl": c.get("scenario_worst_pnl"),
        "event_screen": best.get("event_screen"),
        "path_risk": best.get("path_risk"),
    }
    reason = (
        "mixed-expiration structure cleared conservative economic-loss, assignment-notional, event and liquidity gates and beat stock control"
        if c["strategy_type"] in MIXED_ALL
        else "covered call has exact risk math, cleared liquidity/capital gates, and beat stock control"
    )
    return {
        "status": "OK", "ticker": ticker, "expression": "option_overlay",
        "stock_control_score": stock_score,
        "best_extended_score": best["score"],
        "selected": selected,
        "reason": reason,
        "analysis_only": analysis_only,
        "rejected": rejected,
        "methodology": "heuristic expression comparison after hard risk gates; not expected return, probability of profit, broker margin, or order advice",
    }
