"""Refresh a ticker's latest report with exact source-bound facts only.

Legacy narrative reports remain immutable history. This module writes a new
latest report only when the underlying bundle identity or price basis changes.
It performs no provider call, LLM call, forecast qualification, or trade action.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import db
from .research_contract import bundle_identity, evaluate, read_inputs
from .research_cycle import build_brief
from .report_schema import validate_analysis_report

METHOD = "bounded_source_evidence_brief.v1"


def _same_inputs(report: dict | None, bundle: dict) -> bool:
    report = report or {}
    inputs = report.get("research_inputs") or {}
    market = bundle.get("market_snapshot") or {}
    validation = report.get("claim_validation") or {}
    return (
        report.get("method") == METHOD
        and inputs.get("bundle_sha256") == bundle_identity(bundle)
        and inputs.get("price") == market.get("price")
        and inputs.get("price_date") == market.get("price_date")
        and validation.get("status") == "VERIFIED"
    )


def refresh(ticker: str, *, now: datetime | None = None) -> dict:
    ticker = str(ticker or "").strip().upper()
    if not ticker:
        raise ValueError("TICKER_REQUIRED")
    now = now or datetime.now(timezone.utc)

    bundle, previous, prediction = read_inputs(ticker)
    if _same_inputs(previous, bundle):
        contract = evaluate(bundle, previous, prediction or {}, now=now)
        validation = previous.get("claim_validation") or {}
        return {
            "ticker": ticker,
            "status": "CURRENT",
            "replayed": True,
            "report_id": previous.get("analysis_id"),
            "verified": validation.get("verified", 0),
            "total": validation.get("total", 0),
            "research_status": contract.get("research_status"),
        }

    brief = build_brief(
        bundle,
        previous,
        now=now,
        news={"status": "NOT_COLLECTED", "reason": "SOURCE_CLAIM_REFRESH"},
    )
    validate_analysis_report(
        brief,
        expected_ticker=ticker,
        expected_as_of=brief["as_of"],
        source_bundle=bundle,
    )
    with db.connect() as conn:
        db.save_report(
            conn,
            brief["analysis_id"],
            ticker,
            brief["as_of"],
            brief,
            source_bundle=bundle,
        )
    contract = evaluate(bundle, brief, prediction or {}, now=now)
    validation = brief.get("claim_validation") or {}
    return {
        "ticker": ticker,
        "status": "REFRESHED",
        "replayed": False,
        "report_id": brief["analysis_id"],
        "verified": validation.get("verified", 0),
        "total": validation.get("total", 0),
        "research_status": contract.get("research_status"),
    }
