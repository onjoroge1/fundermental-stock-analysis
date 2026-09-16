"""Bounded fresh evidence briefs, dated facts, counterchecks and immutable runs.

No LLM or broker execution is implied. New source facts and input changes are
reviewed by deterministic checks; external headlines remain unreviewed context.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import db, research_store
from .agents.contracts import PILOT
from .bundle import build_bundle
from .claim_evidence import audit_claims, fact_text, FACT_FIELDS
from .research_contract import bundle_identity, digest, evaluate

def build_brief(bundle: dict, previous: dict | None = None, *, now=None, news=None) -> dict:
    now = now or datetime.now(timezone.utc)
    ticker = bundle["company"]["ticker"]
    quarters = bundle.get("financial_history", {}).get("quarterly_periods", [])
    latest = quarters[-1] if quarters else {}
    claims = []
    for field, (statement, label) in FACT_FIELDS.items():
        value = latest.get(statement, {}).get(field)
        source = latest.get("field_provenance", {}).get(field) or {}
        if value is None or not source.get("source_content_sha256") or source.get("value") != value:
            continue
        evidence = {"path": f"/financial_history/quarterly_periods/{len(quarters)-1}/{statement}/{field}",
                    "value": value, "unit": source["unit"], "tag": source["tag"],
                    "source_id": source["source_id"], "period_end": latest["period_end"], "label": label}
        claims.append({"classification": "FACT", "claim": fact_text(label, value, source["unit"], latest["period_end"]),
                       "source_ids": [source["source_id"]], "evidence": evidence})
    financial = (bundle.get("data_quality") or {}).get("financial_integrity") or {}
    reasons = financial.get("reasons") or []
    prior_facts = {(c.get("evidence") or {}).get("label"): c.get("evidence") for c in (previous or {}).get("claims", [])}
    changes = [{"field": c["evidence"]["label"], "previous": prior_facts[c["evidence"]["label"]], "current": c["evidence"]}
               for c in claims if c["evidence"]["label"] in prior_facts and c["evidence"] != prior_facts[c["evidence"]["label"]]]
    inputs = {"bundle_sha256": bundle_identity(bundle), "price": bundle.get("market_snapshot", {}).get("price"),
              "price_date": bundle.get("market_snapshot", {}).get("price_date")}
    report = {"analysis_schema_version": "1.0.0", "analysis_id": "brief_" + digest({"ticker": ticker, "inputs": inputs, "time": now.isoformat()}),
              "ticker": ticker, "as_of": now.isoformat(), "research_inputs": inputs,
              "method": "bounded_source_evidence_brief.v1", "data_sufficiency": {"status": "WARN" if reasons else "PASS"},
              "fundamental_trend": {"direction": "MIXED", "strength": "WEAK"}, "scenarios": [], "forecasts": {},
              "investment_thesis": {"summary": f"Fresh evidence review for {ticker}: {len(claims)} source-bound facts; {len(changes)} comparable fact changes. Financial interpretation remains limited by the listed dependencies.",
                                     "risks": reasons + ["No predictive edge is asserted by this evidence brief."],
                                     "invalidation_conditions": ["A source is revised or cannot be resolved", "A financial dependency becomes missing", "Price or source inputs change"]},
              "adversarial_review": {"strongest_bear_case": "A sourced fact is not evidence that the stock is mispriced. This brief supplies no calibrated target or trading recommendation.",
                                     "fragile_assumptions": ["The source observation and accounting mapping describe the same entity and period."],
                                     "unresolved_questions": reasons},
              "conclusion": {"classification": "INSUFFICIENT_DATA", "conviction": "LOW"},
              "claims": claims, "fact_changes": changes, "news_context": news or {"status": "NOT_COLLECTED"},
              "limits": {"max_fact_claims": len(FACT_FIELDS), "max_news_items": 5, "llm_calls": 0, "trade_execution": False,
                         "independent_human_review": False, "fresh_reasoning_scope": "deterministic fact/change and dependency review"}}
    report["claim_validation"] = audit_claims(report, bundle)
    return report


def run(ticker: str, request_key: str, *, collect_market_data=True) -> dict:
    if ticker not in PILOT:
        raise ValueError("Fresh research is restricted to the reviewed five-name pilot")
    key = f"{ticker}:{request_key}"
    # Session advisory lock spans provider reads but holds no transaction or
    # partial writes. It prevents duplicate paid calls on concurrent retries.
    with db.connect() as lock_conn:
        lock_conn.autocommit = True
        locked = lock_conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", ("research-cycle:" + key,)).fetchone()[0]
        if not locked:
            return {"status": "BUSY", "ticker": ticker, "request_key": request_key}
        try:
            existing = research_store.get(lock_conn, "CYCLE_RESULT", key)
            if existing:
                return {"replayed": True, **existing["payload"]}
            from .research_contract import read_inputs
            bundle, previous, prediction = read_inputs(ticker)
            provider, news = {"status": "NOT_REQUESTED"}, {"status": "NOT_REQUESTED"}
            if collect_market_data:
                from .ingestion.massive import daily_observation, news_observation, MassiveUnavailable
                try:
                    provider = daily_observation(ticker, bundle["market_snapshot"]["price_date"])
                    with db.connect() as conn:
                        research_store.save(conn, "RAW_SOURCE", provider["source_content_sha256"], provider["raw_response"], ticker)
                    provider.pop("raw_response")
                    spot = bundle["market_snapshot"].get("price")
                    provider["comparison_to_stored_close_pct"] = round((provider["rows"][-1]["close"] / spot - 1) * 100, 4) if spot else None
                    provider["comparison_status"] = "MATCH_WITHIN_1_PERCENT" if spot and abs(provider["comparison_to_stored_close_pct"]) <= 1 else "DISAGREEMENT_OR_MISSING"
                except MassiveUnavailable as exc:
                    provider = {"status": "UNAVAILABLE", "reason": str(exc)}
                # Two calls maximum, no retry loops or hidden paid fallbacks.
                try:
                    news = news_observation(ticker)
                    with db.connect() as conn:
                        research_store.save(conn, "RAW_SOURCE", news["source_content_sha256"], news["raw_response"], ticker)
                    news.pop("raw_response")
                except MassiveUnavailable as exc:
                    news = {"status": "UNAVAILABLE", "reason": str(exc)}
            now = datetime.now(timezone.utc)
            report = build_brief(bundle, previous, now=now, news=news)
            contract = evaluate(bundle, report, prediction, now=now)
            result = {"ticker": ticker, "request_key": request_key, "completed_at": now.isoformat(),
                      "status": "COMPLETED_WITH_WITHHELD_OUTPUTS" if contract["reasons"] else "COMPLETED",
                      "research_contract": contract, "report_id": report["analysis_id"],
                      "provider_status": provider.get("status"), "news_status": news.get("status"),
                      "claims_verified": report["claim_validation"]["verified"], "order_submission": False}
            from .control_plane import build_index_row, save_index_row
            indexed = build_index_row(ticker, bundle=bundle, report=report, prediction=prediction or {})
            with db.connect() as conn:
                # Report persistence validates the actual source bundle again.
                db.save_report(conn, report["analysis_id"], ticker, report["as_of"], report, source_bundle=bundle, commit=False)
                research_store.save(conn, "PROVIDER_DAILY", key, provider, ticker)
                research_store.save(conn, "RESEARCH_SNAPSHOT", contract["snapshot_id"], {"bundle": bundle, "report": report, "forecast": prediction, "contract": contract}, ticker)
                save_index_row(conn, ticker, indexed, commit=False)
                research_store.save(conn, "CYCLE_RESULT", key, result, ticker)
            return {"replayed": False, **result}
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", ("research-cycle:" + key,))
