"""Issuer-specific debt reconciliation must remain pinned and fail closed."""
from __future__ import annotations

from copy import deepcopy

import pytest

from stock_machine.financial_integrity import debt_evidence, reviewed_debt_sources
from stock_machine.normalization.financial_periods import build_periods


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


BALANCE_TAGS = {
    "commercial_paper": "CommercialPaper",
    "short_term_debt": "LongTermDebtCurrent",
    "long_term_debt": "LongTermDebtNoncurrent",
    "cash_and_equivalents": "CashAndCashEquivalentsAtCarryingValue",
    "total_assets": "Assets",
    "total_liabilities": "Liabilities",
    "shareholders_equity": "StockholdersEquity",
}


def _companyfacts(row, duration="quarter"):
    """Synthetic SEC facts with the pinned review's actual values/accession."""
    period = _period(row)
    common = {"end": row["period_end"], "filed": "2026-08-01",
              "accn": row["accession_number"], "fy": 2026,
              "form": "10-K" if duration == "annual" else "10-Q",
              "fp": "FY" if duration == "annual" else "Q2"}
    gaap = {
        "Revenues": {"units": {"USD": [{
            **common, "val": 100,
            "start": "2025-07-01" if duration == "annual" else "2026-04-01",
        }]}}
    }
    for field, value in period["fields"].items():
        gaap[BALANCE_TAGS[field]] = {"units": {"USD": [{**common, "val": value}]}}
    return {"cik": int(row["cik"]), "facts": {"us-gaap": gaap}}


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "UBER", "HIMS", "VZ"])
@pytest.mark.parametrize("duration", ["quarter", "annual"])
def test_refresh_normalizes_both_reviewed_debt_formats(ticker, duration):
    row = next(r for r in reviewed_debt_sources() if r["ticker"] == ticker)
    quarterly, annual, _ = build_periods(_companyfacts(row, duration))
    period = (annual if duration == "annual" else quarterly)[-1]
    assert period["fields"]["_reviewed_debt"] == row
    result = debt_evidence(period)
    assert result["status"] == "VERIFIED"
    assert result["total_debt"] == row["total_debt"]
    assert result["net_debt"] == row["total_debt"] - row["cash_and_equivalents"]


@pytest.mark.parametrize("ticker", ["UBER", "HIMS"])
@pytest.mark.parametrize("mismatch", ["value", "missing", "accession", "cash", "cik", "period"])
def test_refresh_does_not_embed_unbound_reviewed_debt(ticker, mismatch):
    row = next(r for r in reviewed_debt_sources() if r["ticker"] == ticker)
    facts = _companyfacts(row)
    gaap = facts["facts"]["us-gaap"]
    field = next(f for f in row["binding_fields"] if f != "cash_and_equivalents")
    tag = BALANCE_TAGS[field]
    entry = gaap[tag]["units"]["USD"][0]
    if mismatch == "value":
        entry["val"] += 1
    elif mismatch == "missing":
        del gaap[tag]
    elif mismatch == "accession":
        entry["accn"] = "different-filing"
    elif mismatch == "cash":
        gaap[BALANCE_TAGS["cash_and_equivalents"]]["units"]["USD"][0]["val"] += 1
    elif mismatch == "cik":
        facts["cik"] = 1
    else:
        for item in gaap.values():
            item["units"]["USD"][0]["end"] = "2026-06-29"
    quarterly, _, _ = build_periods(facts)
    period = quarterly[-1]
    assert "_reviewed_debt" not in period["fields"]
    # debt_evidence may separately discover a filing by globally unique accession;
    # all value/source/period mismatches must also fail that downstream lookup.
    if mismatch != "cik":
        assert debt_evidence(period)["total_debt"] is None


@pytest.mark.parametrize("ticker", ["UBER", "HIMS"])
@pytest.mark.parametrize("invalid", ["no_bindings", "bad_sum", "empty_arithmetic"])
def test_refresh_rejects_invalid_source_review_contract(monkeypatch, ticker, invalid):
    from stock_machine.normalization import financial_periods

    row = next(r for r in reviewed_debt_sources() if r["ticker"] == ticker)
    evidence = deepcopy(row)
    if invalid == "no_bindings":
        evidence["binding_fields"] = {}
    elif invalid == "bad_sum":
        evidence["total_debt"] += 100
    else:
        evidence["reviewed_components"] = {}
    monkeypatch.setattr(financial_periods, "reviewed_debt_sources", lambda: (evidence,))
    quarterly, _, _ = build_periods(_companyfacts(row))
    assert "_reviewed_debt" not in quarterly[-1]["fields"]
