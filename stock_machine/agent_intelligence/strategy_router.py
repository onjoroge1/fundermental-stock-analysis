"""Explicit eligibility contract shared by routing and exploration (no orders)."""
from __future__ import annotations
from math import isfinite

BULLISH_OPTIONS = {"bull_call_debit_spread", "bull_put_credit_spread", "cash_secured_put", "long_call"}
BEARISH_OPTIONS = {"bear_put_debit_spread", "bear_call_credit_spread", "long_put"}
NEUTRAL_OPTIONS = {"iron_condor"}


def _number(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return None
    value = float(value)
    if (low is not None and value < low) or (high is not None and value > high):
        return None
    return value


def route(state: dict, option_candidates: list | None = None, *,
          max_risk_usd: float = 1000.0, stock_notional_usd: float = 5000.0) -> dict:
    if not _number(max_risk_usd, 0) or not _number(stock_notional_usd, 0):
        raise ValueError("INVALID_ROUTER_BUDGET")
    direction = state.get("direction", "NEUTRAL")
    bias = _number(state.get("bias_score"), -1, 1)
    state_blockers = list(state.get("blockers") or [])
    if bias is None:
        state_blockers.append("INVALID_BIAS")
        bias = 0.0
    if state.get("paper_eligible") is not True:
        state_blockers.append("STATE_NOT_ELIGIBLE")
    state_blockers = sorted(set(state_blockers))
    candidates = [{"action": "NO_TRADE", "instrument": "NONE", "utility": round(max(.25, 1-abs(bias)), 4),
                   "max_risk_usd": 0.0, "eligible": True, "blockers": [], "warnings": [],
                   "reasons": ["Capital preservation is always an available action."]}]
    if direction in {"BULLISH", "BEARISH"}:
        long = direction == "BULLISH"
        candidates.append({"action": "LONG_STOCK" if long else "SHORT_STOCK", "instrument": "STOCK",
                           "utility": round((.35 + .35*abs(bias)) if long else (.25 + .30*abs(bias)), 4),
                           "max_risk_usd": stock_notional_usd if long else None,
                           "notional_usd": stock_notional_usd, "eligible": not state_blockers,
                           "blockers": state_blockers, "warnings": [] if long else ["UNBOUNDED_SHORT_LOSS"],
                           "reasons": ["Directional stock expression; no guaranteed stop-loss cap."]})
    allowed = BULLISH_OPTIONS if direction == "BULLISH" else BEARISH_OPTIONS if direction == "BEARISH" else NEUTRAL_OPTIONS
    for candidate in option_candidates or []:
        row = candidate.model_dump(mode="json") if hasattr(candidate, "model_dump") else dict(candidate)
        strategy = str(row.get("strategy_type") or "")
        if strategy not in allowed:
            continue
        payoff, liquidity, ranking = (row.get(k) or {} for k in ("payoff", "liquidity", "ranking"))
        loss = _number(payoff.get("max_loss"), 0)
        liq, rank = _number(liquidity.get("score"), 0, 1), _number(ranking.get("total"), 0, 100)
        blockers = list(state_blockers)
        if payoff.get("defined_risk") is not True:
            blockers.append("OPTION_RISK_NOT_DEFINED")
        if loss is None or loss <= 0:
            blockers.append("OPTION_MAX_LOSS_INVALID")
        elif loss > max_risk_usd:
            blockers.append("OPTION_MAX_LOSS_EXCEEDS_BUDGET")
        if liquidity.get("passed") is not True or liq is None:
            blockers.append("OPTION_LIQUIDITY_GATE_FAILED")
        if rank is None or not row.get("candidate_id"):
            blockers.append("OPTION_ID_OR_RANK_INVALID")
        utility = 0.0 if blockers else round(.5*rank/100 + .3*(1-loss/max_risk_usd) + .2*liq, 4)
        candidates.append({"action": "OPTION", "instrument": "OPTION", "strategy_type": strategy,
                           "candidate_id": row.get("candidate_id"), "utility": utility, "max_risk_usd": loss,
                           "eligible": not blockers, "blockers": sorted(set(blockers)),
                           "warnings": list(row.get("warnings") or []), "reasons": ["Defined-risk option comparison."]})
    selected = max((c for c in candidates if c["eligible"]), key=lambda c: (c["utility"], c["action"] != "NO_TRADE"))
    return {"schema_version": "strategy-router.v2", "mode": "PAPER_SHADOW_ONLY", "direction": direction,
            "bias_score": bias, "selected": selected, "candidates": sorted(candidates, key=lambda c: -c["utility"]),
            "state_blockers": state_blockers, "selection_reason": "Eligible comparison only; explanations never reject candidates.",
            "broker_submission": False,
            "limitations": ["Heuristic utility is not expected return or probability of profit.",
                            "Stock notional and option maximum loss are not identical risk measures."]}


def eligible_actions(routed: dict) -> dict[str, dict]:
    """One mask for the router and bandit; best candidate per strategy, no overwrites."""
    result = {}
    for candidate in routed.get("candidates") or []:
        if candidate.get("eligible") is not True or candidate.get("blockers"):
            continue
        if routed.get("state_blockers") and candidate.get("action") != "NO_TRADE":
            continue
        key = ("OPTION:" + candidate["strategy_type"] if candidate.get("instrument") == "OPTION"
               else candidate["action"])
        if key not in result or candidate["utility"] > result[key]["utility"]:
            result[key] = candidate
    if "NO_TRADE" not in result:
        raise ValueError("NO_TRADE_ACTION_MISSING")
    return result
