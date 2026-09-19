"""Paper/shadow strategy router across no-trade, stock and defined-risk options."""
from __future__ import annotations

BULLISH_OPTIONS={"bull_call_debit_spread","bull_put_credit_spread","cash_secured_put","long_call"}
BEARISH_OPTIONS={"bear_put_debit_spread","bear_call_credit_spread","long_put"}
NEUTRAL_OPTIONS={"iron_condor"}


def _option_payload(candidate):
    if hasattr(candidate,"model_dump"):
        return candidate.model_dump(mode="json")
    return dict(candidate)


def _option_utility(row: dict, max_risk_usd: float) -> tuple[float,list[str]]:
    reasons=[]
    payoff=row.get("payoff") or {}
    liquidity=row.get("liquidity") or {}
    ranking=row.get("ranking") or {}
    max_loss=payoff.get("max_loss")
    defined=bool(payoff.get("defined_risk"))
    if not defined:
        reasons.append("OPTION_RISK_NOT_DEFINED")
    if max_loss is None:
        reasons.append("OPTION_MAX_LOSS_MISSING")
    elif float(max_loss)>max_risk_usd:
        reasons.append("OPTION_MAX_LOSS_EXCEEDS_BUDGET")
    if not liquidity.get("passed",False):
        reasons.append("OPTION_LIQUIDITY_GATE_FAILED")
    if reasons:
        return 0.0,reasons
    risk_eff=1.0-min(1.0,float(max_loss)/max_risk_usd) if max_risk_usd>0 else 0.0
    base=float(ranking.get("total") or 0)/100.0
    liq=float(liquidity.get("score") or 0)
    return round(.50*base+.30*risk_eff+.20*liq,4),[]


def route(state: dict, option_candidates: list | None = None, *,
          max_risk_usd: float = 1000.0,
          stock_notional_usd: float = 5000.0) -> dict:
    direction=state.get("direction","NEUTRAL")
    bias=abs(float(state.get("bias_score") or 0))
    candidates=[{"action":"NO_TRADE","instrument":"NONE","utility":round(max(.25,1.0-bias),4),
                 "max_risk_usd":0.0,"reasons":["always-available capital-preservation action"]}]
    if state.get("paper_eligible"):
        if direction=="BULLISH":
            candidates.append({"action":"LONG_STOCK","instrument":"STOCK",
                "utility":round(.35+.35*bias,4),"max_risk_usd":stock_notional_usd,
                "reasons":["directional stock expression; downside is not contractually capped"]})
        elif direction=="BEARISH":
            candidates.append({"action":"SHORT_STOCK","instrument":"STOCK",
                "utility":round(.25+.30*bias,4),"max_risk_usd":None,
                "reasons":["short-stock loss is not contractually bounded; penalized versus defined-risk options"]})

    allowed=(BULLISH_OPTIONS if direction=="BULLISH" else
             BEARISH_OPTIONS if direction=="BEARISH" else NEUTRAL_OPTIONS)
    for candidate in option_candidates or []:
        row=_option_payload(candidate)
        strategy=str(row.get("strategy_type") or "")
        if strategy not in allowed:
            continue
        utility,reasons=_option_utility(row,max_risk_usd)
        candidates.append({"action":"OPTION","instrument":"OPTION","strategy_type":strategy,
                           "candidate_id":row.get("candidate_id"),"utility":utility,
                           "max_risk_usd":(row.get("payoff") or {}).get("max_loss"),
                           "reasons":reasons,
                           "eligible":not reasons})
    eligible=[c for c in candidates if not c.get("reasons") or c["action"]=="NO_TRADE"
              or c.get("eligible",c["instrument"]=="STOCK")]
    if state.get("blockers"):
        selected=candidates[0]
        selection_reason="state blockers force NO_TRADE"
    else:
        selected=max(eligible,key=lambda c:(c["utility"],c["action"]!="NO_TRADE"))
        selection_reason="highest transparent paper utility among eligible structures"
    return {
        "schema_version":"strategy-router.v1",
        "mode":"PAPER_SHADOW_ONLY",
        "direction":direction,
        "bias_score":state.get("bias_score"),
        "selected":selected,
        "candidates":sorted(candidates,key=lambda c:-c["utility"]),
        "selection_reason":selection_reason,
        "broker_submission":False,
        "limitations":[
            "utility is a fixed comparison convention, not expected return or probability of profit",
            "defined-risk options are preferred only when current candidate risk/liquidity gates pass",
            "no option or stock order is created by the router",
        ],
    }
