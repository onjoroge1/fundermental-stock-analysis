"""Issuer-specific debt reconciliation must remain pinned and fail closed."""
from __future__ import annotations

import pytest

from stock_machine.financial_integrity import debt_evidence, reviewed_debt_sources


EXPECTED = {
    "AAPL": (84_344_000_000, 39_544_000_000),
    "MSFT": (40_294_000_000, 20_935_000_000),
    "UBER": (12_723_000_000, 4_870_000_000),
    "HIMS": (1_365_299_000, 609_811_000),
}


def _period(row):
    accession = row["accession_number"]
    fields = {}
    fields.update(row.get("components") or {})
    fields.update(row.get("binding_fields") or {})
    fields["cash_and_equivalents"] = row["cash_and_equivalents"]
    sources = {field: accession for field in fields}
    return {
        "period_end": row["period_end"],
        "accession_number": accession,
        "fields": fields,
        "field_sources": sources,
    }


@pytest.mark.parametrize("ticker", sorted(EXPECTED))
def test_reviewed_pilot_debt_is_exact_and_period_bound(ticker):
    row = next(r for r in reviewed_debt_sources() if r["ticker"] == ticker)
    total, cash = EXPECTED[ticker]
    result = debt_evidence(_period(row))
    assert result["status"] == "VERIFIED"
    assert result["basis"] == "REVIEWED_ISSUER_BALANCE_SHEET_TOTAL"
    assert result["total_debt"] == total
    assert result["cash_and_equivalents"] == cash
    assert result["net_debt"] == total - cash
    assert result["source_ids"] == [row["source_id"]]


@pytest.mark.parametrize("ticker", ["UBER", "HIMS"])
def test_source_reviewed_components_require_normalized_same_accession_bindings(ticker):
    row = next(r for r in reviewed_debt_sources() if r["ticker"] == ticker)
    period = _period(row)
    field = next(iter(row["binding_fields"]))
    period["fields"][field] += 1
    result = debt_evidence(period)
    assert result["status"] == "WITHHELD"
    assert result["total_debt"] is None
    assert "TOTAL_DEBT_NOT_SOURCE_RECONCILED" in result["reasons"]


def test_review_never_applies_to_another_accession():
    row = next(r for r in reviewed_debt_sources() if r["ticker"] == "AAPL")
    period = _period(row)
    period["accession_number"] = "different-filing"
    period["field_sources"] = {k: "different-filing" for k in period["field_sources"]}
    result = debt_evidence(period)
    assert result["status"] == "WITHHELD"
    assert result["total_debt"] is None
