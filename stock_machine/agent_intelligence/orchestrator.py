"""Frozen, serialized v2 evaluation. Research is SHADOW; no broker actions."""
from __future__ import annotations
from datetime import datetime, timezone
from .features import build_for_ticker
from .news_events import build as build_news
from .state import assemble
from .strategy_router import route, eligible_actions
from . import bandit


def _regime(conn, ticker: str, as_of: str):
    from .. import db
    from ..regime import RegimeFeatureProvider, sector_etf
    company = db.fetch_company(conn, ticker) or {}
    spy, qqq = db.fetch_prices(conn, "SPY", as_of), db.fetch_prices(conn, "QQQ", as_of)
    symbol = sector_etf(company.get("sector"))
    sector = db.fetch_prices(conn, symbol, as_of) if symbol else []
    if not spy:
        return {"status": "UNAVAILABLE", "classification": "UNKNOWN"}
    return RegimeFeatureProvider(spy, qqq_rows=qqq, sector_rows=sector).features_as_of(as_of)


def _observed_at(decision: dict) -> datetime:
    stamp = datetime.fromisoformat(str(decision.get("observed_at") or decision.get("decided_at") or "").replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
        raise ValueError("INTELLIGENCE_DECISION_TIME_INVALID")
    return stamp.astimezone(timezone.utc)


def evaluate_decision(decision: dict, *, option_candidates: list | None = None, mode: str = "SHADOW") -> dict:
    if mode not in {"SHADOW", "PAPER"}:
        raise ValueError("INTELLIGENCE_MODE_INVALID")
    ticker, key, input_sha = decision.get("ticker"), decision.get("decision_id"), decision.get("input_sha256")
    if not ticker or not key or not input_sha:
        raise ValueError("INTELLIGENCE_DECISION_IDENTITY_MISSING")
    from .. import db, research_store
    from ..options.surface_store import latest_as_of
    with db.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("intelligence:" + str(key),))
        existing = research_store.get(conn, "AGENT_INTELLIGENCE_V2", str(key))
        if existing:
            old = existing["payload"]
            if old.get("mode") != mode or old.get("input_sha256", input_sha) != input_sha:
                raise ValueError("INTELLIGENCE_REPLAY_IDENTITY_OR_MODE_CONFLICT")
            return {**old, "replayed": True}
        observed = _observed_at(decision)
        row = conn.execute("SELECT payload FROM agent_lab_evidence WHERE input_sha256=%s", (input_sha,)).fetchone()
        if not row:
            raise ValueError("INTELLIGENCE_FROZEN_EVIDENCE_NOT_FOUND")
        packet = row[0]
        if packet.get("ticker") != ticker:
            raise ValueError("INTELLIGENCE_EVIDENCE_TICKER_MISMATCH")
        as_of = decision.get("price_date")
        if not as_of or (packet.get("market_snapshot") or {}).get("price_date") != as_of:
            raise ValueError("INTELLIGENCE_PRICE_DATE_MISMATCH")
        technical = build_for_ticker(conn, ticker, as_of=as_of)
        regime = _regime(conn, ticker, as_of)
        surface = latest_as_of(conn, ticker, observed.isoformat(), max_age_days=10)
        latest = research_store.latest(conn, "AGENT_BANDIT_STATE_V1", ticker)
        arms = (latest or {}).get("payload", {}).get("arms", {})
        news = build_news((packet.get("analysis") or {}).get("news_context") or {}, now=observed)
        bundle = {"fundamental_scores": (packet.get("fundamentals") or {}).get("fundamental_scores") or {},
                  "market_snapshot": packet.get("market_snapshot") or {}, "data_quality": packet.get("data_quality") or {}}
        state = assemble(ticker, bundle, technical, news, option_surface=surface, regime=regime)
        if decision.get("status") != "RECORDED":
            state["blockers"] = sorted(set(state.get("blockers", []) + ["AGENT_DECISION_NOT_RECORDED"]))
            state["paper_eligible"] = False
        routed = route(state, option_candidates or [])
        choices = eligible_actions(routed)
        selection = bandit.select(state, sorted(choices), arms, mode=mode)
        selected_key = selection["selected"]["action"]
        if selected_key not in choices:
            raise ValueError("INELIGIBLE_BANDIT_ACTION")
        selected = choices[selected_key]
        instruction = None
        if mode == "PAPER":
            instruction = {"desired_side": {"LONG_STOCK": "LONG", "SHORT_STOCK": "SHORT"}.get(selected_key, "FLAT"),
                           "source": "agent-intelligence.v2", "selected_action": selected_key}
            if routed["state_blockers"]:
                instruction["blocker"] = "INTELLIGENCE_STATE_BLOCKED"
            elif selected_key.startswith("OPTION:"):
                instruction["blocker"] = "OPTION_PAPER_EXECUTOR_NOT_CONNECTED"
        result = {"schema_version": "agent-intelligence.v2", "status": "OK", "mode": mode,
                  "decision_id": str(key), "ticker": ticker, "input_sha256": input_sha,
                  "observed_at": observed.isoformat(), "state": state, "router": routed,
                  "bandit": selection, "selected": selected, "paper_instruction": instruction,
                  "broker_submission": False}
        research_store.save(conn, "AGENT_INTELLIGENCE_V2", str(key), result, ticker)
    return result
