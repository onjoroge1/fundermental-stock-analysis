"""One dated eligibility contract used by every research consumer."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, timedelta

from .claim_evidence import audit_claims
from .market_calendar import latest_completed_session

VERSION = "research-contract.v1"
MAX_REPORT_AGE = timedelta(hours=36)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode()).hexdigest()


def timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def bundle_identity(bundle):
    return digest({k: bundle.get(k) for k in ("company", "financial_history", "derived_metrics", "consensus", "invalidation_breaches")}
                  | {"market": {k: (bundle.get("market_snapshot") or {}).get(k) for k in ("price", "price_date", "source_ids")},
                     "datasets": (bundle.get("data_quality") or {}).get("dataset_versions", {})})


def read_inputs(ticker: str):
    """One repeatable database view, shared by HTTP, index and cycle writers."""
    from . import db
    from .bundle import build_bundle
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        bundle = build_bundle(ticker, connection=conn)
        report = db.latest_report(conn, ticker)
        prediction = db.latest_prediction_forecast(conn, ticker)
    return bundle, report, prediction


def evaluate(bundle: dict, report: dict | None, prediction: dict | None,
             *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    report, prediction = report or {}, prediction or {}
    market, quality = bundle.get("market_snapshot") or {}, bundle.get("data_quality") or {}
    ticker = (bundle.get("company") or {}).get("ticker")
    reasons = []
    report_at = timestamp(report.get("as_of"))
    if not report_at:
        reasons.append("REPORT_DATE_MISSING_OR_INVALID")
    elif report_at > now:
        reasons.append("REPORT_FROM_FUTURE")
    elif now - report_at > MAX_REPORT_AGE:
        reasons.append("REPORT_EXPIRED")
    if report.get("ticker") != ticker:
        reasons.append("REPORT_IDENTITY_MISMATCH")
    if quality.get("status") != "PASS" or quality.get("critical_missing_fields") or quality.get("stale_datasets"):
        reasons.append("REQUIRED_DATA_NOT_VERIFIED")
    financial = quality.get("financial_integrity") or {}
    if financial.get("status") != "VERIFIED":
        reasons.append("FINANCIAL_DEPENDENCIES_UNVERIFIED")
    expected = str(latest_completed_session(now))
    if market.get("price_date") != expected:
        reasons.append("PRICE_NOT_LATEST_COMPLETED_SESSION")
    inputs = report.get("research_inputs") or {}
    bundle_hash = bundle_identity(bundle)
    if inputs.get("bundle_sha256") != bundle_hash:
        reasons.append("REPORT_INPUTS_SUPERSEDED_OR_UNBOUND")
    if inputs.get("price_date") != market.get("price_date") or inputs.get("price") != market.get("price"):
        reasons.append("REPORT_PRICE_BASIS_DIFFERS")
    if bundle.get("invalidation_breaches"):
        reasons.append("THESIS_INVALIDATION_REQUIRES_REVIEW")
    claims = audit_claims(report, bundle)
    if claims["status"] != "VERIFIED":
        reasons.append("CLAIM_EVIDENCE_UNVERIFIED")
    forecast_at = timestamp(prediction.get("generated_at"))
    model_reasons = []
    if (prediction.get("as_of") or "")[:10] != expected:
        model_reasons.append("FORECAST_NOT_CURRENT_SESSION")
    if not forecast_at or forecast_at > now:
        model_reasons.append("FORECAST_GENERATION_DATE_INVALID")
    if prediction.get("ticker") != ticker:
        model_reasons.append("FORECAST_IDENTITY_MISMATCH")
    distribution = prediction.get("forecast_distribution") or {}
    qualified = []
    try:
        from .forecasts.models import ForecastDistribution
        from .prediction import MIN_CALIBRATION_SAMPLES, MIN_EVALUATION_SAMPLES
        parsed = ForecastDistribution.model_validate(distribution)
        if parsed.symbol != ticker or parsed.as_of.isoformat() != expected or parsed.spot_price != market.get("price"):
            model_reasons.append("FORECAST_INPUT_BASIS_DIFFERS")
        qualified = [r.horizon_days for r in parsed.horizons
                     if r.readiness_status == "VALIDATED" and r.calibration_status == "calibrated"
                     and r.baseline_status == "beats_baseline"
                     and r.calibration_samples >= MIN_CALIBRATION_SAMPLES
                     and r.validation_samples >= MIN_EVALUATION_SAMPLES]
    except (ValueError, TypeError):
        model_reasons.append("FORECAST_CONTRACT_INVALID_OR_MISSING")
    if not qualified:
        model_reasons.append("MODEL_NOT_QUALIFIED")
    # Scenarios are explicitly uncalibrated analyst judgments until a separate
    # persisted validation supports their actual horizon and target.
    guidance_reasons = reasons + model_reasons
    # No current report has independently persisted horizon-specific scenario
    # validation. A report author cannot self-certify with a boolean field.
    guidance_reasons.append("ANALYST_SCENARIOS_UNQUALIFIED")
    identity = {"ticker": ticker, "bundle_sha256": bundle_hash,
                "report_sha256": digest(report), "forecast_sha256": digest(prediction)}
    return {"schema_version": VERSION, "snapshot_id": "research_" + digest(identity),
            "input_identity": identity, "evaluated_at": now.isoformat(),
            "report_id": report.get("analysis_id"), "report_as_of": report.get("as_of"),
            "report_expires_at": (report_at + MAX_REPORT_AGE).isoformat() if report_at else None,
            "price_date": market.get("price_date"), "price_basis": market.get("price"),
            "expected_price_date": expected, "forecast_id": prediction.get("forecast_id"),
            "forecast_as_of": prediction.get("as_of"), "forecast_generated_at": prediction.get("generated_at"),
            "actual_primary_model": distribution.get("primary_model") or prediction.get("primary_model"),
            "financial_integrity": financial, "claim_validation": claims,
            "research_observation_eligible": not reasons, "guidance_eligible": not guidance_reasons,
            "research_status": "CURRENT_VERIFIED" if not reasons else "WITHHELD",
            "model_status": "QUALIFIED" if not model_reasons else "WITHHELD",
            "reasons": sorted(set(reasons)), "guidance_reasons": sorted(set(guidance_reasons)),
            "qualified_horizons_sessions": qualified,
            "freshness_policy": {"max_report_age_hours": 36, "exact_input_and_price_basis_required": True}}


def safe_analysis(report, contract):
    report = report or {}
    source_brief = report.get("method") == "bounded_source_evidence_brief.v1"
    audit = contract.get("claim_validation") or {}
    verified_indices = {r["claim_index"] for r in audit.get("claims", []) if r["status"] == "VERIFIED"}
    return {"report_available": bool(report), "analysis_id": report.get("analysis_id"),
            "as_of": report.get("as_of"), "status": contract["research_status"],
            "method": report.get("method"), "claim_validation": audit,
            "claims": [c for i, c in enumerate(report.get("claims", [])) if i in verified_indices],
            "forecasts": report.get("forecasts", {}) if contract["guidance_eligible"] else {},
            "scenarios": report.get("scenarios", []) if contract["guidance_eligible"] else [],
            "investment_thesis": report.get("investment_thesis", {}) if contract["guidance_eligible"] else
                {"summary": f"{audit.get('verified', 0)} exact source facts verified. Narrative guidance is withheld until its financial dependencies and predictive claims are independently qualified.",
                 "risks": contract["reasons"], "invalidation_conditions": []},
            "adversarial_review": report.get("adversarial_review", {}) if contract["guidance_eligible"] else
                {"strongest_bear_case": "Correct source facts do not establish that a stock is mispriced or predict its return.", "fragile_assumptions": contract["guidance_reasons"]},
            "fundamental_trend": report.get("fundamental_trend", {}) if contract["guidance_eligible"] else {},
            "news_context": report.get("news_context") if source_brief else None,
            "fact_changes": report.get("fact_changes", []) if source_brief else [],
            "conclusion": report.get("conclusion", {}) if contract["guidance_eligible"] else {"classification": "INSUFFICIENT_DATA", "conviction": "LOW"},
            "historical_context_only": not contract["research_observation_eligible"],
            "withheld_reasons": contract["guidance_reasons"]}


def guard_coverage_row(row: dict, *, now=None) -> dict:
    """Re-evaluate time on every cached read; a cache cannot renew a report."""
    row = dict(row)
    contract = dict(row.get("research_contract") or {})
    expiry = timestamp(contract.get("report_expires_at"))
    now = now or datetime.now(timezone.utc)
    expected = str(latest_completed_session(now))
    if (contract.get("schema_version") != VERSION or not expiry or expiry < now
            or contract.get("price_date") != expected):
        contract["guidance_eligible"] = False
        contract["research_observation_eligible"] = False
        contract["research_status"] = "WITHHELD"
        contract["guidance_reasons"] = sorted(set(contract.get("guidance_reasons", []) + ["CACHED_CONTRACT_EXPIRED_OR_MISSING"]))
    if not contract.get("guidance_eligible"):
        row["report_12m"] = None
    if (contract.get("financial_integrity") or {}).get("status") != "VERIFIED":
        row["composite_score"] = None
        row["ev_to_revenue_ttm"] = None
        row["signal_count"] = 0
        row["signals"] = {k: False for k in row.get("signals", {})}
    row["research_contract"] = contract
    return row
