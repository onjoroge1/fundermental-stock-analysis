"""Live-cycle assembly for Agent Intelligence v2.

Research mode runs SHADOW intelligence. PAPER mode may drive the existing
simulated equity executor and may create a separate append-only simulated option
entry from a current defined-risk candidate. Nothing here submits a broker order.
"""
from __future__ import annotations

from .features import build_for_ticker
from .news_events import build as build_news
from .state import assemble
from .strategy_router import route
from . import bandit


def _regime(conn, ticker: str, as_of: str | None):
    from .. import db
    from ..regime import RegimeFeatureProvider, sector_etf
    company=db.fetch_company(conn,ticker) or {}
    spy=db.fetch_prices(conn,"SPY",as_of)
    qqq=db.fetch_prices(conn,"QQQ",as_of)
    sector_symbol=sector_etf(company.get("sector"))
    sector=db.fetch_prices(conn,sector_symbol,as_of) if sector_symbol else []
    if not spy:
        return {"status":"UNAVAILABLE","classification":"UNKNOWN"}
    return RegimeFeatureProvider(spy,qqq_rows=qqq,sector_rows=sector).features_as_of(as_of or spy[-1]["date"])


def _action_key(candidate: dict) -> str:
    if candidate.get("instrument")=="OPTION":
        return "OPTION:"+str(candidate.get("strategy_type") or "UNKNOWN")
    return str(candidate.get("action") or "NO_TRADE")


def evaluate_decision(decision: dict, *, option_candidates: list | None=None,
                      mode: str="SHADOW") -> dict:
    ticker=str(decision.get("ticker") or "").upper()
    input_sha=decision.get("input_sha256")
    if not ticker or not input_sha:
        raise ValueError("INTELLIGENCE_DECISION_IDENTITY_MISSING")
    from .. import db, research_store
    from ..options.surface_store import latest_as_of
    decision_id = str(decision.get("decision_id") or "")
    if not decision_id:
        raise ValueError("INTELLIGENCE_DECISION_ID_MISSING")
    with db.connect() as conn:
        existing = research_store.get(conn, "AGENT_INTELLIGENCE_V2", decision_id)
        if existing:
            return {"replayed": True, **existing["payload"]}
        row=conn.execute("SELECT payload FROM agent_lab_evidence WHERE input_sha256=%s",(input_sha,)).fetchone()
        if not row:
            raise ValueError("INTELLIGENCE_FROZEN_EVIDENCE_NOT_FOUND")
        packet=row[0] or {}
        as_of=decision.get("price_date") or (packet.get("market_snapshot") or {}).get("price_date")
        technical=build_for_ticker(conn,ticker,as_of=as_of)
        regime=_regime(conn,ticker,as_of)
        surface=latest_as_of(conn,ticker,str(packet.get("generated_at") or ""),max_age_days=10)
        latest_bandit=research_store.latest(conn,"AGENT_BANDIT_STATE_V1",ticker)
        arm_states=(latest_bandit or {}).get("payload",{}).get("arms",{})

    news=build_news(((packet.get("analysis") or {}).get("news_context") or {}))
    pseudo_bundle={
        "fundamental_scores":((packet.get("fundamentals") or {}).get("fundamental_scores") or {}),
        "market_snapshot":packet.get("market_snapshot") or {},
        "data_quality":packet.get("data_quality") or {},
    }
    state=assemble(ticker,pseudo_bundle,technical,news,option_surface=surface,regime=regime)
    option_context = {"status": "NOT_REQUESTED", "candidates": option_candidates or []}
    if mode == "PAPER" and option_candidates is None and state.get("paper_eligible"):
        try:
            from .option_bridge import generate as generate_options
            option_context = generate_options(ticker, state.get("direction") or "NEUTRAL",
                                              max_risk_usd=1000.0)
            option_candidates = option_context.get("candidates") or []
            if option_context.get("surface"):
                state=assemble(ticker,pseudo_bundle,technical,news,
                               option_surface=option_context.get("surface"),regime=regime)
        except Exception as exc:
            option_context = {
                "status": "UNAVAILABLE",
                "reason": f"{type(exc).__name__}: option candidate bridge unavailable",
                "candidates": [],
                "broker_submission": False,
            }
            option_candidates = []
    routed=route(state,option_candidates or [])
    action_candidates=[]
    by_key={}
    for candidate in routed["candidates"]:
        if candidate.get("reasons") and candidate.get("action")!="NO_TRADE" and not candidate.get("eligible",False):
            continue
        key=_action_key(candidate)
        action_candidates.append(key)
        by_key[key]=candidate
    if "NO_TRADE" not in action_candidates:
        action_candidates.append("NO_TRADE")
        by_key["NO_TRADE"]={"action":"NO_TRADE","instrument":"NONE","utility":1.0}
    bmode="PAPER" if mode=="PAPER" else "SHADOW"
    selection=bandit.select(state,action_candidates,arm_states,mode=bmode)
    selected_key=selection["selected"]["action"]
    selected=by_key[selected_key]
    result={
        "schema_version":"agent-intelligence.v2",
        "decision_id":decision.get("decision_id"),
        "ticker":ticker,
        "mode":bmode,
        "state":state,
        "router":routed,
        "bandit":selection,
        "selected":selected,
        "option_context":option_context,
        "option_paper":None,
        "paper_instruction":None,
        "broker_submission":False,
    }
    if bmode=="PAPER":
        if selected_key=="LONG_STOCK":
            result["paper_instruction"]={"desired_side":"LONG","source":"agent-intelligence.v2",
                                         "selected_action":selected_key}
        elif selected_key=="SHORT_STOCK":
            result["paper_instruction"]={"desired_side":"SHORT","source":"agent-intelligence.v2",
                                         "selected_action":selected_key}
        elif selected_key=="NO_TRADE":
            result["paper_instruction"]={"desired_side":"FLAT","source":"agent-intelligence.v2",
                                         "selected_action":selected_key}
        else:
            chosen_id = selected.get("candidate_id")
            chosen = next((x for x in (option_candidates or [])
                           if x.get("candidate_id") == chosen_id), None)
            if chosen is None:
                result["paper_instruction"]={"desired_side":"FLAT","source":"agent-intelligence.v2",
                                             "selected_action":selected_key,
                                             "blocker":"OPTION_CANDIDATE_NOT_FOUND"}
            else:
                from .option_paper import open_entry
                result["option_paper"] = open_entry(ticker, decision_id, chosen)
                result["paper_instruction"]={"desired_side":"FLAT","source":"agent-intelligence.v2",
                                             "selected_action":selected_key,
                                             "blocker":"OPTION_SELECTED_SEPARATE_PAPER_LEDGER"}
    with db.connect() as conn:
        research_store.save(conn,"AGENT_INTELLIGENCE_V2",decision_id,result,ticker)
    return result
