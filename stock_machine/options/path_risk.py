"""Conservative capital-risk assessment for mixed-expiration option structures.

This module is intentionally separate from pricing.  P2-C introduced
front-expiry mark-to-model valuation for calendars and diagonals.  P2-D adds a
fail-closed capital-risk contract that can decide whether those structures are
safe enough to enter automated expression comparison.

The contract distinguishes two different risks:

1. economic loss bound: a conservative front-expiry loss bound using only the
   far option's intrinsic-value floor.  This does not depend on IV remaining
   unchanged and therefore does not confuse a scenario model with a risk bound;
2. assignment liquidity exposure: the temporary stock/cash notional that can
   appear if the American short option is assigned before or at the front
   expiration.  This is not called broker margin because actual broker margin
   depends on account type and broker rules.

No order placement occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import fabs

MIXED_TYPES = {
    "call_calendar",
    "put_calendar",
    "call_diagonal",
    "put_diagonal",
}


@dataclass(frozen=True)
class PathRiskPolicy:
    """Pre-committed gates for mixed-expiration automation eligibility."""

    max_economic_loss_multiple_of_budget: float = 1.0
    max_assignment_notional_multiple_of_budget: float = 2.5
    require_positive_debit: bool = True
    minimum_liquidity_score: float = 0.55
    require_event_screen: bool = True


def _economic_loss_bound(candidate: dict) -> tuple[float | None, list[str]]:
    """Return a conservative front-expiry economic-loss bound.

    The far option must be worth at least intrinsic value.  At the front
    expiry, the short leg is at most its intrinsic obligation.  This gives a
    model-independent lower bound on spread value and therefore a conservative
    loss ceiling before transaction costs/financing.

    Calls: adverse strike gap exists when far strike > near strike.
    Puts: adverse strike gap exists when far strike < near strike.
    """
    reasons: list[str] = []
    strategy = candidate.get("strategy_type")
    if strategy not in MIXED_TYPES:
        return None, ["path-risk contract only supports calendars/diagonals"]

    near = candidate.get("near_leg") or {}
    far = candidate.get("far_leg") or {}
    try:
        near_strike = float(near["strike"])
        far_strike = float(far["strike"])
        debit = float(candidate["net_debit"])
    except (KeyError, TypeError, ValueError):
        return None, ["candidate lacks near/far strikes or net debit"]

    if debit < 0:
        reasons.append("net-credit mixed-expiration structures are not bounded by this debit-risk contract")
        return None, reasons

    right = str(near.get("right") or far.get("right") or "").upper()
    if right == "C":
        adverse_gap = max(0.0, far_strike - near_strike)
    elif right == "P":
        adverse_gap = max(0.0, near_strike - far_strike)
    else:
        return None, ["option right must be C or P"]

    # net_debit is already contract dollars in P2-C; strike gap needs x100.
    bound = max(0.0, debit + adverse_gap * 100.0)
    return round(bound, 2), reasons


def _assignment_notional(candidate: dict) -> tuple[float | None, list[str]]:
    """Conservative temporary notional created by short-option assignment."""
    near = candidate.get("near_leg") or {}
    try:
        strike = float(near["strike"])
    except (KeyError, TypeError, ValueError):
        return None, ["candidate lacks near-leg strike"]
    right = str(near.get("right") or "").upper()
    spot = float(candidate.get("spot_price") or 0.0)
    if right == "P":
        # Put assignment purchases 100 shares for strike cash.
        notional = strike * 100.0
    elif right == "C":
        # Call assignment creates short stock. Use the larger of current spot
        # and strike so a currently-ITM call does not understate stock notional.
        notional = max(spot, strike) * 100.0
    else:
        return None, ["near-leg option right must be C or P"]
    return round(notional, 2), []


def assess_mixed_path_risk(candidate: dict, position_budget: float,
                           event_screen: dict | None = None,
                           policy: PathRiskPolicy | None = None) -> dict:
    """Assess whether a mixed-expiration candidate can enter P2 comparison.

    ``event_screen`` is deliberately external to this module.  P2-D refuses to
    assume there is no dividend/earnings/assignment catalyst.  A later event
    ingestion layer can populate the same contract without changing risk math.
    """
    policy = policy or PathRiskPolicy()
    strategy = candidate.get("strategy_type")
    reasons: list[str] = []
    warnings: list[str] = []

    if candidate.get("status") != "OK":
        reasons.append("extended valuation did not pass")
    if strategy not in MIXED_TYPES:
        reasons.append("candidate is not a supported mixed-expiration structure")
    if position_budget <= 0:
        reasons.append("position budget must be positive")

    debit = candidate.get("net_debit")
    try:
        debit_value = float(debit)
    except (TypeError, ValueError):
        debit_value = None
        reasons.append("net debit unavailable")
    if policy.require_positive_debit and (debit_value is None or debit_value <= 0):
        reasons.append("positive net debit required for automation")

    liquidity = float(candidate.get("liquidity_score") or 0.0)
    if liquidity < policy.minimum_liquidity_score:
        reasons.append("liquidity score below path-risk policy minimum")

    economic_loss, econ_reasons = _economic_loss_bound(candidate)
    reasons.extend(econ_reasons)
    assignment_notional, assignment_reasons = _assignment_notional(candidate)
    reasons.extend(assignment_reasons)

    if economic_loss is not None and position_budget > 0:
        if economic_loss > position_budget * policy.max_economic_loss_multiple_of_budget:
            reasons.append("conservative economic-loss bound exceeds position budget")
    if assignment_notional is not None and position_budget > 0:
        if assignment_notional > position_budget * policy.max_assignment_notional_multiple_of_budget:
            reasons.append("temporary assignment notional exceeds policy multiple of position budget")

    event_status = "UNKNOWN"
    if event_screen:
        event_status = str(event_screen.get("status") or "UNKNOWN").upper()
        if event_status == "BLOCK":
            reasons.extend(event_screen.get("reasons") or ["event screen blocked mixed-expiration automation"])
        elif event_status == "CLEAR":
            pass
        else:
            warnings.append("event screen did not return CLEAR/BLOCK")
    if policy.require_event_screen and event_status != "CLEAR":
        reasons.append("clear event/assignment screen required for automation")

    return {
        "status": "OK" if not reasons else "REJECT",
        "strategy_type": strategy,
        "automation_eligible": not reasons,
        "position_budget": round(position_budget, 2),
        "conservative_economic_max_loss": economic_loss,
        "economic_loss_multiple_of_budget": (
            round(economic_loss / position_budget, 4)
            if economic_loss is not None and position_budget > 0 else None
        ),
        "transient_assignment_notional": assignment_notional,
        "assignment_notional_multiple_of_budget": (
            round(assignment_notional / position_budget, 4)
            if assignment_notional is not None and position_budget > 0 else None
        ),
        "event_screen_status": event_status,
        "reasons": list(dict.fromkeys(reasons)),
        "warnings": list(dict.fromkeys(warnings + [
            "assignment notional is a liquidity exposure, not a broker margin estimate",
            "economic loss bound excludes commissions, financing, slippage, taxes and execution delay",
            "American exercise can create temporary stock exposure before the far option is closed or exercised",
        ])),
        "methodology": (
            "far-option intrinsic-value floor + short-leg intrinsic obligation; "
            "separate transient assignment notional; fail closed on unknown events"
        ),
    }
