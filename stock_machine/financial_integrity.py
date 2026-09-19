"""Financial dependency checks. Missing disclosures never mean zero.

Only a disclosed total, complete current/noncurrent subtotals, or a reviewed
same-period source reconciliation establishes total debt. Legacy partial sums
are deliberately withheld until the source evidence has been recovered.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

VERSION = "financial-integrity.v1"


@lru_cache(maxsize=1)
def reviewed_debt_sources() -> tuple[dict, ...]:
    """Reviewed period-specific debt reconciliations pinned to exact filings."""
    path = Path(__file__).parent / "normalization" / "reviewed_balance_sheets.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("reviewed debt evidence must be a list")
    return tuple(rows)


def _matching_review(period: dict, fields: dict, sources: dict) -> dict:
    embedded = fields.get("_reviewed_debt")
    if isinstance(embedded, dict) and embedded:
        return embedded
    accession = period.get("accession_number") or sources.get("cash_and_equivalents")
    if not accession or not period.get("period_end"):
        return {}
    matches = [
        row for row in reviewed_debt_sources()
        if row.get("period_end") == period.get("period_end")
        and row.get("accession_number") == accession
    ]
    return matches[0] if len(matches) == 1 else {}


def _matches_bound_fields(fields: dict, sources: dict, accession: str, expected: dict) -> bool:
    return bool(expected) and all(
        number(value) is not None
        and number(fields.get(field)) == number(value)
        and sources.get(field) == accession
        for field, value in expected.items()
    )


def _reviewed_total(period: dict, fields: dict, sources: dict) -> tuple[float | None, dict]:
    """Validate reviewed arithmetic without treating missing normalized tags as zero.

    Normalized components must match exactly. Source-reviewed components are
    facts read directly from the pinned filing and require separate binding
    fields tied to normalized facts from the same accession.
    """
    reviewed = _matching_review(period, fields, sources)
    accession = reviewed.get("accession_number")
    total = number(reviewed.get("total_debt"))
    if (reviewed.get("status") != "SOURCE_RECONCILED"
            or not accession or not reviewed.get("source_url") or not reviewed.get("source_id")
            or total is None or total < 0):
        return None, {}

    normalized = reviewed.get("components") or {}
    source_reviewed = reviewed.get("reviewed_components") or {}
    bindings = reviewed.get("binding_fields") or {}
    arithmetic = source_reviewed or normalized
    if (not arithmetic
            or any(number(v) is None or number(v) < 0 for v in arithmetic.values())
            or abs(sum(float(v) for v in arithmetic.values()) - total) > 1):
        return None, {}
    if normalized and not _matches_bound_fields(fields, sources, accession, normalized):
        return None, {}
    if source_reviewed and not _matches_bound_fields(fields, sources, accession, bindings):
        return None, {}
    if bindings and not _matches_bound_fields(fields, sources, accession, bindings):
        return None, {}

    reviewed_cash = number(reviewed.get("cash_and_equivalents"))
    if reviewed_cash is not None:
        if (number(fields.get("cash_and_equivalents")) != reviewed_cash
                or sources.get("cash_and_equivalents") != accession):
            return None, {}
    return total, reviewed


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def debt_evidence(period: dict | None) -> dict:
    p = period or {}
    fields = p.get("fields") or {}
    sources = p.get("field_sources") or {}
    provenance = fields.get("_financial_provenance") or {}
    reviewed = fields.get("_reviewed_debt") or {}
    reasons = []
    total = None
    basis = None
    source_ids = []
    debt_accession = None
    direct = number(fields.get("reported_total_debt"))
    if direct is not None and direct >= 0 and sources.get("reported_total_debt"):
        total, basis = direct, "DISCLOSED_TOTAL_DEBT_AND_FINANCE_LEASES"
        source_ids = ["SEC:ACCESSION:" + sources["reported_total_debt"]]
        debt_accession = sources["reported_total_debt"]
    else:
        current = number(fields.get("debt_current_total"))
        noncurrent = number(fields.get("debt_noncurrent_total"))
        accession = sources.get("debt_current_total")
        if (current is not None and noncurrent is not None and min(current, noncurrent) >= 0
                and accession and sources.get("debt_noncurrent_total") == accession):
            total, basis = current + noncurrent, "COMPLETE_CURRENT_PLUS_NONCURRENT_DEBT_AND_FINANCE_LEASES"
            source_ids = ["SEC:ACCESSION:" + accession]
            debt_accession = accession
        else:
            reviewed_total, reviewed = _reviewed_total(p, fields, sources)
            if reviewed_total is not None:
                total, basis = reviewed_total, "REVIEWED_ISSUER_BALANCE_SHEET_TOTAL"
                source_ids = [reviewed["source_id"]]
                debt_accession = reviewed["accession_number"]
            else:
                reasons.append("TOTAL_DEBT_NOT_SOURCE_RECONCILED")
    if total is not None:
        liabilities = number(fields.get("total_liabilities"))
        if liabilities is not None and total > liabilities * 1.01:
            reasons.append("DEBT_EXCEEDS_TOTAL_LIABILITIES")
            total = None
    cash = number(fields.get("cash_and_equivalents"))
    if cash is None or cash < 0:
        reasons.append("CASH_AND_EQUIVALENTS_MISSING_OR_INVALID")
        cash = None
    elif not sources.get("cash_and_equivalents"):
        reasons.append("CASH_SOURCE_MISSING")
        cash = None
    elif debt_accession and sources["cash_and_equivalents"] != debt_accession:
        reasons.append("CASH_DEBT_SOURCE_ACCESSIONS_DIFFER")
        cash = None
    # The definition is explicit: cash and equivalents only. Securities are
    # shown separately; their missing value is never imputed to zero.
    net = total - cash if total is not None and cash is not None else None
    return {"schema_version": VERSION, "status": "VERIFIED" if net is not None else "WITHHELD",
            "total_debt": total, "net_debt": net, "cash_and_equivalents": cash,
            "definition": "disclosed debt less cash and cash equivalents; excludes investment securities",
            "basis": basis, "source_ids": source_ids, "reasons": reasons,
            "period_end": p.get("period_end"), "field_provenance": provenance}


def balance_sheet_check(period: dict | None) -> dict:
    p = period or {}
    fields, sources = p.get("fields") or {}, p.get("field_sources") or {}
    required = ("total_assets", "total_liabilities", "shareholders_equity")
    missing = [f for f in required if number(fields.get(f)) is None or not sources.get(f)]
    residual = None
    reasons = ["MISSING_BALANCE_FIELD:" + f for f in missing]
    if not missing:
        basis = {sources[f] for f in required}
        extras = ("noncontrolling_interest", "temporary_equity", "redeemable_noncontrolling_interest")
        # An including-NCI equity total already includes noncontrolling equity.
        eq_tag = ((fields.get("_financial_provenance") or {}).get("shareholders_equity") or {}).get("tag")
        if eq_tag == "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest":
            extras = ("temporary_equity", "redeemable_noncontrolling_interest")
        included = [f for f in extras if number(fields.get(f)) is not None]
        basis.update(sources.get(f) for f in included)
        if len(basis) != 1 or None in basis:
            reasons.append("BALANCE_SOURCE_ACCESSIONS_DIFFER")
        residual = fields["total_assets"] - fields["total_liabilities"] - fields["shareholders_equity"] - sum(fields[f] for f in included)
        if fields["total_assets"] <= 0 or abs(residual) > .01 * fields["total_assets"]:
            reasons.append("BALANCE_IDENTITY_FAILED")
    debt = debt_evidence(p)
    reasons.extend(debt["reasons"])
    return {"schema_version": VERSION, "status": "VERIFIED" if not reasons else "WITHHELD",
            "period_end": p.get("period_end"), "available_at": p.get("available_at"),
            "identity_residual": residual, "identity_tolerance_fraction": .01,
            "debt": debt, "reasons": reasons,
            "scope": "source identity, accounting identity and debt dependencies; not independent assurance of every disclosure"}
