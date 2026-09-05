"""Synthetic companyfacts covering the spec's normalization failure modes:
Q4 derivation, first-reported-wins restatement policy, duplicate cumulative
contexts, and non-additive fields excluded from subtraction."""
from stock_machine.normalization.financial_periods import (
    build_periods, extract_shares_outstanding)


def _entry(val, start, end, filed, form="10-Q", fy=2025, fp="Q1",
           accn="0000-25-000001"):
    return {"val": val, "start": start, "end": end, "filed": filed,
            "form": form, "fy": fy, "fp": fp, "accn": accn}


def synthetic_facts():
    revenue = [
        _entry(100, "2024-10-01", "2024-12-31", "2025-02-01", fp="Q1"),
        _entry(110, "2025-01-01", "2025-03-31", "2025-05-01", fp="Q2"),
        _entry(120, "2025-04-01", "2025-06-30", "2025-08-01", fp="Q3"),
        # 6-month cumulative context: must be ignored, not double counted
        _entry(210, "2024-10-01", "2025-03-31", "2025-05-01", fp="Q2"),
        # annual FY (Q4 = 500 - 330 = 170)
        _entry(500, "2024-10-01", "2025-09-30", "2025-11-15", form="10-K", fp="FY"),
        # restatement of Q1 in a later filing: first-reported must win
        _entry(105, "2024-10-01", "2024-12-31", "2025-05-01", fp="Q2",
               accn="0000-25-000002"),
    ]
    eps = [
        _entry(1.0, "2024-10-01", "2024-12-31", "2025-02-01", fp="Q1"),
        _entry(1.1, "2025-01-01", "2025-03-31", "2025-05-01", fp="Q2"),
        _entry(1.2, "2025-04-01", "2025-06-30", "2025-08-01", fp="Q3"),
        _entry(4.6, "2024-10-01", "2025-09-30", "2025-11-15", form="10-K", fp="FY"),
    ]
    assets = [
        _entry(1000, None, "2024-12-31", "2025-02-01", fp="Q1"),
        _entry(1100, None, "2025-09-30", "2025-11-15", form="10-K", fp="FY"),
    ]
    for a in assets:
        a.pop("start")
        a["start"] = None
    return {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": revenue}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": eps}},
        "Assets": {"units": {"USD": assets}},
    }}}


def test_q4_derived_and_available_after_10k_filing_day():
    quarterly, annual, events = build_periods(synthetic_facts())
    q4 = [q for q in quarterly if q["period_end"] == "2025-09-30"]
    assert len(q4) == 1
    q4 = q4[0]
    assert q4["derived"] is True
    assert q4["fields"]["revenue"] == 170
    assert q4["available_at"] == "2025-11-16"  # not knowable before the 10-K


def test_non_additive_fields_not_subtracted():
    quarterly, _, _ = build_periods(synthetic_facts())
    q4 = [q for q in quarterly if q["period_end"] == "2025-09-30"][0]
    assert "diluted_eps" not in q4["fields"]  # 4.6 - 3.3 would be nonsense


def test_first_reported_wins_and_restatement_logged():
    quarterly, _, events = build_periods(synthetic_facts())
    q1 = [q for q in quarterly if q["period_end"] == "2024-12-31"][0]
    assert q1["fields"]["revenue"] == 100  # original, not the restated 105
    assert q1["available_at"] == "2025-02-02"
    restatements = [e for e in events if e["event"] == "RESTATEMENT"]
    assert len(restatements) == 1
    assert restatements[0]["later_values"][0]["value"] == 105


def test_cumulative_context_ignored():
    quarterly, _, _ = build_periods(synthetic_facts())
    ends = [q["period_end"] for q in quarterly]
    assert ends.count("2025-03-31") == 1  # the 6-month context created no period


def test_balance_sheet_attached_to_derived_q4():
    quarterly, _, _ = build_periods(synthetic_facts())
    q4 = [q for q in quarterly if q["period_end"] == "2025-09-30"][0]
    assert q4["fields"]["total_assets"] == 1100


def test_shares_outstanding_extraction():
    cf = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {
        "shares": [{"val": 1000, "end": "2025-01-15", "filed": "2025-02-01"},
                   {"val": 990, "end": "2025-04-15", "filed": "2025-05-01"}]
    }}}}}
    rows = extract_shares_outstanding(cf)
    assert [r["shares"] for r in rows] == [1000, 990]
    assert rows[0]["available_at"] == "2025-02-02"


def test_ytd_cumulative_differencing_fills_cash_flow():
    """10-Q cash-flow statements are YTD: Q2 OCF must be derived as 6mo − Q1."""
    ocf = [
        _entry(50, "2024-10-01", "2024-12-31", "2025-02-01", fp="Q1"),
        _entry(90, "2024-10-01", "2025-03-31", "2025-05-01", fp="Q2"),   # 6mo YTD
        _entry(150, "2024-10-01", "2025-06-30", "2025-08-01", fp="Q3"),  # 9mo YTD
        _entry(200, "2024-10-01", "2025-09-30", "2025-11-15", form="10-K", fp="FY"),
    ]
    revenue = [
        _entry(100, "2024-10-01", "2024-12-31", "2025-02-01", fp="Q1"),
        _entry(110, "2025-01-01", "2025-03-31", "2025-05-01", fp="Q2"),
        _entry(120, "2025-04-01", "2025-06-30", "2025-08-01", fp="Q3"),
        _entry(500, "2024-10-01", "2025-09-30", "2025-11-15", form="10-K", fp="FY"),
    ]
    cf = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": revenue}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
    }}}
    quarterly, _, _ = build_periods(cf)
    by_end = {q["period_end"]: q for q in quarterly}
    assert by_end["2024-12-31"]["fields"]["operating_cash_flow"] == 50
    assert by_end["2025-03-31"]["fields"]["operating_cash_flow"] == 40  # 90-50
    assert by_end["2025-06-30"]["fields"]["operating_cash_flow"] == 60  # 150-90
    assert by_end["2025-09-30"]["fields"]["operating_cash_flow"] == 50  # FY − 3Q


def test_share_counts_in_millions_rescaled_against_dei():
    """MCD/T/TXN/DAL/LUV file weighted shares in millions; the dei cover-page
    count confirms the 1e6 scale and the fact is rescaled with an event."""
    cf = {"facts": {
        "us-gaap": {
            "Revenues": {"units": {"USD": [
                _entry(100, "2025-01-01", "2025-03-31", "2025-05-01", fp="Q1")]}},
            "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {
                "shares": [_entry(718.2, "2025-01-01", "2025-03-31",
                                  "2025-05-01", fp="Q1")]}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 710_505_859, "end": "2025-04-15", "filed": "2025-05-01"}]}}},
    }}
    quarterly, _, events = build_periods(cf)
    q = quarterly[-1]
    assert q["fields"]["weighted_average_diluted_shares"] == 718.2e6
    assert any(e["event"] == "SHARE_SCALE_CORRECTED" for e in events)


def test_real_share_counts_not_rescaled():
    cf = {"facts": {
        "us-gaap": {
            "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {
                "shares": [_entry(718_200_000, "2025-01-01", "2025-03-31",
                                  "2025-05-01", fp="Q1")]}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 710_505_859, "end": "2025-04-15", "filed": "2025-05-01"}]}}},
    }}
    quarterly, _, events = build_periods(cf)
    assert quarterly[-1]["fields"]["weighted_average_diluted_shares"] == 718_200_000
    assert not any(e["event"] == "SHARE_SCALE_CORRECTED" for e in events)


def test_corrupt_per_share_fact_dropped():
    cf = {"facts": {"us-gaap": {
        "EarningsPerShareDiluted": {"units": {"USD/shares": [
            _entry(12_162_578.0, "2025-01-01", "2025-03-31", "2025-05-01", fp="Q1")]}},
    }}}
    quarterly, _, events = build_periods(cf)
    assert all("diluted_eps" not in q["fields"] for q in quarterly)
    assert any(e["event"] == "CORRUPT_FACT_DROPPED" for e in events)


def test_bank_revenue_uses_total_net_revenue_tag():
    """RevenuesNetOfInterestExpense outranks Revenues for bank filers."""
    cf = {"facts": {"us-gaap": {
        "RevenuesNetOfInterestExpense": {"units": {"USD": [
            _entry(1_100_368_000, "2026-01-01", "2026-03-31", "2026-05-01", fp="Q1")]}},
        "Revenues": {"units": {"USD": [
            _entry(600_000_000, "2026-01-01", "2026-03-31", "2026-05-01", fp="Q1")]}},
    }}}
    quarterly, _, _ = build_periods(cf)
    assert quarterly[-1]["fields"]["revenue"] == 1_100_368_000
