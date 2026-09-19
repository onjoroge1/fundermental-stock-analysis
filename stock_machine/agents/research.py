"""Evidence extraction, not an LLM or a qualified trading strategy.

All records are prospective observations. The current research API is not a
historical replay source. Quoted analyst text is untrusted, attributed context.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from uuid import uuid4

from .contracts import Decision, PILOT, digest, utc_now


def _obj(value):
    return value if isinstance(value, dict) else {}


def _quoted(value) -> str | None:
    if value in (None, "", [], {}):
        return None
    from .contracts import canonical
    text = value if isinstance(value, str) else canonical(value)
    return text[:11950] + " [truncated; see frozen evidence]" if len(text) > 12000 else text


def build_decision(ticker: str, packet: dict, observed_at: datetime,
                   expected_session: str, previous_id: str | None = None) -> Decision:
    """Build a bounded research record from the packet actually captured now."""
    if ticker not in PILOT:
        raise ValueError("Ticker is outside the frozen research pilot")
    input_hash = digest(packet)  # reject malformed/nonfinite data before saving
    blockers = []
    from ..research_contract import VERSION, timestamp
    contract = _obj(packet.get("research_contract"))
    if contract.get("schema_version") != VERSION or not contract.get("snapshot_id"):
        blockers.append("DATED_RESEARCH_CONTRACT_MISSING")
    if not contract.get("research_observation_eligible"):
        blockers.extend(contract.get("reasons") or ["RESEARCH_CONTRACT_NOT_VERIFIED"])
    expiry = timestamp(contract.get("report_expires_at"))
    if not expiry or expiry < observed_at:
        blockers.append("REPORT_EXPIRED_AT_CAPTURE")
    if packet.get("ticker") != ticker:
        blockers.append("PACKET_IDENTITY_MISMATCH")
    try:
        generated = datetime.fromisoformat(str(packet.get("generated_at", "")).replace("Z", "+00:00"))
        if generated.utcoffset() is None or generated > observed_at:
            blockers.append("INVALID_OR_FUTURE_PACKET_TIMESTAMP")
    except ValueError:
        blockers.append("MISSING_PACKET_TIMESTAMP")
    quality = _obj(packet.get("data_quality"))
    if quality.get("status") != "PASS":
        blockers.append("DATA_QUALITY_NOT_PASS")
    if quality.get("critical_missing_fields"):
        blockers.append("CRITICAL_INPUTS_MISSING")
    if quality.get("stale_datasets"):
        blockers.append("STALE_INPUTS")
    versions = _obj(quality.get("dataset_versions"))
    for name in ("fundamentals", "prices", "filings"):
        version = _obj(versions.get(name))
        # Fundamentals manifests are content-addressed. A historical WARN may
        # reflect an older validation rule even when the exact same content now
        # passes the current source/debt reconciliation. Keep the immutable hash
        # requirement, reject hard FAIL, and let current data_quality +
        # financial_integrity determine whether a WARN has been resolved.
        invalid_status = (version.get("status") == "FAIL" if name == "fundamentals"
                          else version.get("status") != "PASS")
        if invalid_status or not version.get("content_hash"):
            blockers.append("UNVERIFIED_" + name.upper())
        try:
            seen = datetime.fromisoformat(str(version.get("observed_at", "")).replace("Z", "+00:00"))
            if seen.utcoffset() is None or seen > observed_at:
                blockers.append("INVALID_OR_FUTURE_" + name.upper() + "_VINTAGE")
        except ValueError:
            blockers.append("MISSING_" + name.upper() + "_VINTAGE_TIME")
    market = _obj(packet.get("market_snapshot"))
    price = market.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        blockers.append("MISSING_VALID_PRICE")
    price_date = market.get("price_date")
    if price_date != expected_session:
        blockers.append("PRICE_NOT_LATEST_COMPLETED_SESSION")
    # No borrowing a 12-month scenario as a calibrated 20-session forecast.
    analysis = _obj(packet.get("analysis"))
    thesis = _obj(analysis.get("investment_thesis"))
    if not analysis.get("report_available") or not thesis.get("summary"):
        blockers.append("THESIS_MISSING")
    model = _obj(packet.get("model_distribution"))
    limitations = (
        "Research only: no simulated or broker order was submitted.",
        "Existing analyst text is attributed context, not new independently verified reasoning.",
        "A 12-month thesis does not validate a 20-session strategy.",
        "This recorder does not collect news or run an LLM; source briefs may include explicitly unreviewed news metadata. Exploration, fills, P&L and rewards are disabled.",
        "Recorded now; not evidence that this packet was available at a historical decision date.",
    )
    pointers = ("/research_contract", "/analysis/investment_thesis", "/analysis/adversarial_review",
                "/data_quality", "/market_snapshot", "/model_distribution")
    return Decision(
        decision_id=str(uuid4()), ticker=ticker, observed_at=observed_at,
        decided_at=utc_now(), input_sha256=input_hash,
        status="BLOCKED" if blockers else "RECORDED",
        action="NO_TRADE" if blockers else "WATCH",
        rationale=("Research prerequisites failed; no trade is permitted. See recorded blockers."
                   if blockers else "Record the existing thesis for observation. Strategy qualification and execution are not enabled."),
        source_thesis=_quoted(thesis.get("summary")),
        source_counterargument=_quoted(analysis.get("adversarial_review")),
        source_invalidation=_quoted(thesis.get("invalidation_conditions")),
        evidence_pointers=pointers, blockers=tuple(blockers), limitations=limitations,
        source_model_status=str(model.get("status") or "MISSING"),
        source_model_version=model.get("model_version"),
        price_date=price_date if isinstance(price_date, str) else None,
        research_snapshot_id=contract.get("snapshot_id"),
        source_report_id=contract.get("report_id"),
        source_report_as_of=contract.get("report_as_of"),
        research_contract_version=contract.get("schema_version"),
        previous_decision_id=previous_id,
    )


def failure_decision(ticker: str, observed_at: datetime, previous_id: str | None = None):
    """Never persist raw provider exceptions, credentials or a made-up packet."""
    evidence = {"ticker": ticker, "status": "UNAVAILABLE", "error_code": "RESEARCH_READ_FAILED"}
    return evidence, Decision(
        decision_id=str(uuid4()), ticker=ticker, observed_at=observed_at,
        decided_at=utc_now(), status="FAILED", action="NO_TRADE",
        input_sha256=digest(evidence), rationale="Research could not be captured; no trade was authorized.",
        blockers=("RESEARCH_READ_FAILED",),
        limitations=("No research packet was captured. Investigate server-side with the request identifier.",),
        previous_decision_id=previous_id,
    )
