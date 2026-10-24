"""A reviewed balance field is a dated source fact, so its claim must verify."""
from datetime import datetime, timezone

import pytest

from stock_machine.bundle import _period_json, STATEMENT_FIELDS
from stock_machine.normalization import financial_periods as fp
from stock_machine.report_schema import validate_analysis_report
from stock_machine.research_cycle import build_brief

CIK = "0000000042"
END = "2026-06-30"
FILED = "2026-08-01"
NOW = datetime(2026, 9, 16, 15, tzinfo=timezone.utc)

REVIEWS = {
    "total_liabilities": {
        "value": 6_000_000_000, "method": "CURRENT_PLUS_FACE_NONCURRENT_LIABILITY_LINES",
        "components": {"us-gaap:LiabilitiesCurrent": 2_000_000_000,
                       "us-gaap:LongTermDebtNoncurrent": 4_000_000_000},
        "binding_fields": {"total_assets": 10_000_000_000, "shareholders_equity": 4_000_000_000,
                           "current_liabilities": 2_000_000_000}},
    "cash_and_equivalents": {
        "value": 500_000_000, "method": "FACE_CASH_LINE", "components": {"us-gaap:Cash": 500_000_000},
        "binding_fields": {"total_assets": 10_000_000_000, "current_liabilities": 2_000_000_000}},
}


def review(field, accession="A-1"):
    return {"ticker": "ACME", "cik": CIK, "period_end": END, "accession_number": accession,
            "status": "SOURCE_RECONCILED", "field": field, "rounding_bound": 0,
            "source_id": f"SEC:ACME:{END}:REVIEWED_{field.upper()}",
            "source_url": "https://www.sec.gov/x.htm", "source_sha256": "0" * 64, **REVIEWS[field]}


def companyfacts(missing):
    def fact(val, start=None):
        return {"units": {"USD": [{"val": val, "end": END, "filed": FILED, "form": "10-Q", "fy": 2026, "fp": "Q2",
                                   "accn": "A-1", **({"start": start} if start else {})}]}}
    gaap = {"Revenues": fact(5_000_000_000, start="2026-04-01"),
            "Assets": fact(10_000_000_000), "StockholdersEquity": fact(4_000_000_000),
            "LiabilitiesCurrent": fact(2_000_000_000), "Liabilities": fact(6_000_000_000),
            "CashAndCashEquivalentsAtCarryingValue": fact(500_000_000)}
    gaap.pop({"total_liabilities": "Liabilities", "cash_and_equivalents": "CashAndCashEquivalentsAtCarryingValue"}[missing])
    return {"cik": int(CIK), "facts": {"us-gaap": gaap}}


@pytest.mark.parametrize("field", sorted(REVIEWS))
def test_reviewed_field_claim_verifies_with_the_filing_date(monkeypatch, field):
    monkeypatch.setattr(fp, "reviewed_balance_fields", lambda: (review(field),))
    latest = fp.build_periods(companyfacts(field))[0][-1]
    provenance = latest["fields"]["_financial_provenance"][field]
    assert provenance["tag"] == "REVIEWED:" + REVIEWS[field]["method"]
    bundle = {"company": {"ticker": "ACME"},
              "financial_history": {"quarterly_periods": [_period_json(latest, STATEMENT_FIELDS)]}}
    brief = build_brief(bundle, now=NOW)
    claimed = [c["evidence"]["path"].rsplit("/", 1)[1] for c in brief["claims"]]
    row = brief["claim_validation"]["claims"][claimed.index(field)]
    assert row["reasons"] == [] and row["status"] == "VERIFIED"  # was SOURCE_AFTER_REPORT
    assert brief["claim_validation"]["status"] == "VERIFIED"
    validate_analysis_report(brief, source_bundle=bundle)  # what the nightly report save runs
    # The filing's date, never available_at (the following day).
    assert provenance["filed_at"] == FILED and latest["available_at"] == "2026-08-02"


def test_reviewed_field_without_a_same_accession_fact_uses_the_period_filing_date(monkeypatch):
    monkeypatch.setattr(fp, "reviewed_balance_fields", lambda: (review("total_liabilities", accession="B-9"),))
    monkeypatch.setattr(fp, "validate_reviewed_field", lambda evidence, fields, sources: evidence["value"])
    latest = fp.build_periods(companyfacts("total_liabilities"))[0][-1]
    assert latest["fields"]["_financial_provenance"]["total_liabilities"]["filed_at"] == latest["filed_at"] == FILED


def test_reviewed_field_keeps_its_own_filing_date_when_a_later_filing_moves_the_period(monkeypatch):
    monkeypatch.setattr(fp, "reviewed_balance_fields", lambda: (review("total_liabilities"),))
    facts = companyfacts("total_liabilities")
    facts["facts"]["us-gaap"]["AccountsPayableCurrent"] = {"units": {"USD": [
        {"val": 1, "end": END, "filed": "2026-09-20", "form": "10-Q/A", "fy": 2026, "fp": "Q2", "accn": "A-2"}]}}
    latest = fp.build_periods(facts)[0][-1]
    assert latest["filed_at"] == "2026-09-20"                   # the period now dates from the amendment
    assert latest["fields"]["_financial_provenance"]["total_liabilities"]["filed_at"] == FILED   # the review's own filing
