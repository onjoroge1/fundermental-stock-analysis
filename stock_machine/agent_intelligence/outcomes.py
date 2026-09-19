"""Prospective 20-session outcome scoring for Agent Intelligence v2.

Only stock and NO_TRADE actions can currently earn bandit rewards because their
daily path is directly observable from persisted underlying prices. Option paper
entries may settle at expiry separately, but are not rewarded until path-risk
valuation is available.
"""
from __future__ import annotations

from .learning import record_outcome
from .. import db
from ..market_calendar import latest_completed_session, session_offset

HORIZON_SESSIONS=20
ROUND_TRIP_COST_PCT=0.20
CAPITAL_USED_PCT=10.0
TURNOVER_PCT=20.0


def _drawdown(values: list[float]) -> float:
    if not values:
        raise ValueError("OUTCOME_PATH_EMPTY")
    peak=values[0]
    worst=0.0
    for value in values:
        if peak>0:
            worst=min(worst,(value/peak-1.0)*100.0)
        peak=max(peak,value)
    return worst


def _stock_outcome(conn, ticker: str, action: str, entry: str, exit_day: str) -> dict:
    if action=="NO_TRADE":
        return {"status":"MATURED","gross_return_pct":0.0,"max_drawdown_pct":0.0,
                "capital_used_pct":0.0,"turnover_pct":0.0,"costs_pct":0.0,
                "entry_date":entry,"exit_date":exit_day}
    rows=db.fetch_prices(conn,ticker,exit_day)
    by_date={r["date"]:r for r in rows if entry<=r["date"]<=exit_day}
    first=by_date.get(entry)
    last=by_date.get(exit_day)
    if not first or not last:
        raise ValueError("OUTCOME_ENDPOINT_PRICE_MISSING")
    p0=float(first.get("adj_close") or first.get("close") or 0)
    p1=float(last.get("adj_close") or last.get("close") or 0)
    if min(p0,p1)<=0:
        raise ValueError("OUTCOME_ENDPOINT_PRICE_INVALID")
    sign=1.0 if action=="LONG_STOCK" else -1.0
    ordered=[by_date[d] for d in sorted(by_date)]
    values=[]
    for row in ordered:
        price=float(row.get("adj_close") or row.get("close") or 0)
        if price<=0:
            raise ValueError("OUTCOME_PATH_PRICE_INVALID")
        values.append(1.0 + sign*(price/p0-1.0))
    gross=sign*(p1/p0-1.0)*100.0
    return {"status":"MATURED","gross_return_pct":round(gross,6),
            "max_drawdown_pct":round(_drawdown(values),6),
            "capital_used_pct":CAPITAL_USED_PCT,
            "turnover_pct":TURNOVER_PCT,
            "costs_pct":ROUND_TRIP_COST_PCT,
            "entry_date":entry,"exit_date":exit_day,
            "entry_adjusted_close":p0,"exit_adjusted_close":p1,
            "observations":len(values)}


def score_matured(*, limit: int=100) -> dict:
    from .. import research_store
    if not 1<=limit<=1000:
        raise ValueError("OUTCOME_LIMIT_INVALID")
    with db.connect() as conn:
        records=research_store.records(conn,"AGENT_INTELLIGENCE_V2",limit=limit)
    latest=str(latest_completed_session())
    results=[]
    for record in records:
        run=record.get("payload") or {}
        ticker=str(run.get("ticker") or "")
        decision_id=str(run.get("decision_id") or "")
        action=((run.get("bandit") or {}).get("selected") or {}).get("action")
        entry=((run.get("state") or {}).get("as_of"))
        if not ticker or not decision_id or not action or not entry:
            results.append({"status":"SKIPPED_INVALID_RUN","decision_id":decision_id})
            continue
        with db.connect() as conn:
            prior=research_store.get(conn,"AGENT_REWARD_V2",decision_id)
        if prior:
            results.append({"status":"ALREADY_SCORED","ticker":ticker,"decision_id":decision_id})
            continue
        if str(action).startswith("OPTION:"):
            from .option_paper import settle_if_matured
            try:
                option_result=settle_if_matured(ticker,decision_id)
            except ValueError as exc:
                option_result={"status":"UNAVAILABLE","reason":str(exc),
                               "broker_submission":False}
            results.append({"status":"OPTION_"+option_result.get("status","UNKNOWN"),
                            "ticker":ticker,"decision_id":decision_id,
                            "option_outcome":option_result})
            continue
        if action not in {"LONG_STOCK","SHORT_STOCK","NO_TRADE"}:
            results.append({"status":"SKIPPED_ACTION","ticker":ticker,
                            "decision_id":decision_id,"action":action})
            continue
        due=session_offset(entry,HORIZON_SESSIONS)
        if latest<due:
            results.append({"status":"PENDING_MATURITY","ticker":ticker,
                            "decision_id":decision_id,"due_session":due})
            continue
        try:
            with db.connect() as conn:
                outcome=_stock_outcome(conn,ticker,action,entry,due)
            learned=record_outcome(ticker,decision_id,outcome)
            results.append({"status":"SCORED","ticker":ticker,
                            "decision_id":decision_id,"action":action,
                            "outcome":outcome,
                            "reward":learned["reward_record"]["reward"]})
        except ValueError as exc:
            results.append({"status":"BLOCKED_INPUTS","ticker":ticker,
                            "decision_id":decision_id,"reason":str(exc)})
    return {"schema_version":"agent-intelligence-outcomes.v1",
            "latest_completed_session":latest,
            "horizon_sessions":HORIZON_SESSIONS,
            "results":results,
            "scored":sum(r["status"]=="SCORED" for r in results),
            "pending":sum(r["status"]=="PENDING_MATURITY" for r in results),
            "broker_submission":False}
