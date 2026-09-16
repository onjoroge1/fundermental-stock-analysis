"""Resolve claims against the actual source-backed bundle, not declared IDs.

Legacy prose is retained for review, but an identifier alone cannot establish
entailment. New mechanical FACT claims bind an exact path, value and source;
free-form inferences require a separate reviewer and remain unqualified here.
"""
from __future__ import annotations

import json
FACT_FIELDS = {
    "revenue": ("income_statement", "Revenue"),
    "operating_income": ("income_statement", "Operating income"),
    "net_income": ("income_statement", "Net income"),
    "total_assets": ("balance_sheet", "Total assets"),
    "total_liabilities": ("balance_sheet", "Total liabilities"),
    "cash_and_equivalents": ("balance_sheet", "Cash and equivalents"),
    "long_term_debt": ("balance_sheet", "Noncurrent debt"),
    "operating_cash_flow": ("cash_flow", "Operating cash flow"),
}


def pointer(document, path):
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("invalid evidence pointer")
    value = document
    for bit in path[1:].split("/"):
        bit = bit.replace("~1", "/").replace("~0", "~")
        value = value[int(bit)] if isinstance(value, list) else value[bit]
    return value


def source_registry(bundle: dict) -> dict:
    registry = {}
    for period in (bundle.get("financial_history") or {}).get("quarterly_periods", []) + (bundle.get("financial_history") or {}).get("annual_periods", []):
        for entry in (period.get("field_provenance") or {}).values():
            if not isinstance(entry, dict):
                continue
            sid = entry.get("source_id")
            if sid and entry.get("source_url") and entry.get("source_content_sha256"):
                registry.setdefault(sid, []).append(entry)
    return registry


def fact_text(label: str, value, unit: str, period_end: str) -> str:
    return f"{label}: {json.dumps(value, allow_nan=False)} {unit}; period ended {period_end}."


def audit_claims(report: dict | None, bundle: dict) -> dict:
    report = report or {}
    registry = source_registry(bundle)
    rows = []
    for i, claim in enumerate(report.get("claims") or []):
        kind = claim.get("classification")
        if kind == "FORECAST":
            continue  # explicitly judgment, never a verified financial fact
        refs = claim.get("source_ids") or []
        reasons = []
        unresolved = [s for s in refs if s not in registry]
        if not refs or unresolved:
            reasons.append("SOURCE_NOT_RESOLVED")
        evidence = claim.get("evidence") or {}
        try:
            value = pointer(bundle, evidence["path"])
            source = evidence["source_id"]
            parts = evidence["path"].split("/")
            if (len(parts) != 6 or parts[1] != "financial_history"
                    or parts[2] not in ("quarterly_periods", "annual_periods")
                    or not parts[3].isdigit()
                    or FACT_FIELDS.get(parts[5]) != (parts[4], evidence.get("label"))):
                raise ValueError("unsupported financial fact path or label")
            period = bundle["financial_history"][parts[2]][int(parts[3])]
            field_source = period.get("field_provenance", {}).get(parts[5], {})
            matching = [r for r in registry.get(source, []) if r == field_source and r.get("value") == value
                        and r.get("period_end") == evidence.get("period_end")
                        and r.get("unit") == evidence.get("unit")
                        and r.get("tag") == evidence.get("tag")]
            if value != evidence["value"] or source not in refs or not matching:
                reasons.append("EVIDENCE_VALUE_OR_SOURCE_MISMATCH")
            if any(str(r.get("filed_at") or "9999")[:10] > str(report.get("as_of") or "")[:10] for r in matching):
                reasons.append("SOURCE_AFTER_REPORT")
            if str(period.get("available_at") or "9999")[:10] > str(report.get("as_of") or "")[:10]:
                reasons.append("SOURCE_NOT_AVAILABLE_AT_REPORT")
            if kind != "FACT" or claim.get("claim") != fact_text(evidence["label"], value, evidence["unit"], evidence["period_end"]):
                reasons.append("PROSE_ENTAILMENT_REQUIRES_REVIEW")
        except (KeyError, IndexError, TypeError, ValueError):
            reasons.append("STRUCTURED_EVIDENCE_MISSING")
        rows.append({"claim_index": i, "status": "VERIFIED" if not reasons else "UNVERIFIED",
                     "source_ids": refs, "unresolved_source_ids": unresolved, "reasons": reasons})
    verified = sum(r["status"] == "VERIFIED" for r in rows)
    return {"schema_version": "claim-evidence.v1", "status": "VERIFIED" if rows and verified == len(rows) else "UNVERIFIED",
            "verified": verified, "total": len(rows), "claims": rows,
            "meaning": "exact structured source facts only; source-ID presence does not verify prose"}
