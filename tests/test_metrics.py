from stock_machine.features import metrics
from stock_machine.features.scoring import composite, score_all


def _q(end, start, **fields):
    return {"period_end": end, "period_start": start, "available_at": end,
            "fields": fields}


def make_quarters(n=8, revenue0=100.0, growth=0.05):
    """n quarters of steadily growing synthetic data."""
    qs = []
    rev = revenue0
    for i in range(n):
        year = 2024 + i // 4
        month = 3 * (i % 4) + 3
        end = f"{year}-{month:02d}-28"
        start = f"{year}-{month - 2:02d}-01"
        qs.append(_q(end, start,
                     revenue=rev, net_income=rev * 0.2, diluted_eps=rev * 0.002,
                     operating_income=rev * 0.25, operating_cash_flow=rev * 0.3,
                     capital_expenditures=rev * 0.05, free_cash_flow=rev * 0.25,
                     total_assets=rev * 10, shareholders_equity=rev * 4,
                     current_assets=rev * 2, current_liabilities=rev,
                     cash_and_equivalents=rev, long_term_debt=rev * 0.5,
                     weighted_average_diluted_shares=1000.0))
        rev *= 1 + growth
    return qs


def test_ttm_sums_flows_and_takes_latest_balance():
    qs = make_quarters(8)
    ttm = metrics.build_ttm(qs)
    expected_rev = sum(q["fields"]["revenue"] for q in qs[-4:])
    assert abs(ttm["fields"]["revenue"] - expected_rev) < 1e-9
    assert ttm["fields"]["total_assets"] == qs[-1]["fields"]["total_assets"]
    assert ttm["source_periods"] == [q["period_end"] for q in qs[-4:]]


def test_ttm_requires_four_quarters():
    assert metrics.build_ttm(make_quarters(3)) is None


def test_growth_yoy():
    qs = make_quarters(8, growth=0.05)
    g = metrics.growth_metrics(qs, [])
    # 4 quarters of 5% compounding ≈ 21.55%
    assert abs(g["revenue_yoy_pct"] - 21.55) < 0.1


def test_net_debt_sign():
    q = _q("2025-03-31", "2025-01-01", cash_and_equivalents=500.0,
           long_term_debt=200.0)
    assert metrics.net_debt(q) == -300.0  # net cash is negative net debt


def test_valuation_none_safe_when_no_price():
    v = metrics.valuation_metrics(None, None, None, None)
    assert all(val is None for val in v.values())


def test_composite_renormalizes_missing_components():
    scores = {"growth": 80.0, "valuation": 60.0, "expectations": None,
              "profitability": None, "earnings_quality": None,
              "financial_health": None, "capital_allocation": None}
    out = composite(scores)
    # weights 0.15 and 0.20 renormalize to 3/7 and 4/7
    assert abs(out["composite_score"] - (80 * 3 / 7 + 60 * 4 / 7)) < 0.1
    assert out["weights_used"]["valuation"] > out["weights_used"]["growth"]


def test_score_all_marks_expectations_none_without_consensus():
    qs = make_quarters(8)
    ttm = metrics.build_ttm(qs)
    derived = {
        "growth": metrics.growth_metrics(qs, []),
        "profitability": metrics.profitability_metrics(ttm),
        "earnings_quality": metrics.earnings_quality_metrics(ttm, qs),
        "financial_health": metrics.financial_health_metrics(qs[-1], ttm),
        "capital_allocation": metrics.capital_allocation_metrics(ttm, qs, 1e6),
        "valuation": metrics.valuation_metrics(ttm, 100.0, 1e6, 1.1e6),
    }
    out = score_all(derived)
    assert out["components"]["expectations"] is None
    assert "expectations" not in out["weights_used"]
    assert out["composite_score"] is not None


def test_expectations_score_needs_four_surprises():
    from stock_machine.features.scoring import score_expectations
    assert score_expectations(None) is None
    assert score_expectations([]) is None
    assert score_expectations([{"surprise_pct": 5.0}] * 3) is None


def test_expectations_score_beats_vs_misses():
    from stock_machine.features.scoring import score_expectations
    beats = [{"surprise_pct": 6.0}] * 8
    misses = [{"surprise_pct": -6.0}] * 8
    sb, sm = score_expectations(beats), score_expectations(misses)
    assert sb is not None and sm is not None and sb > 70 > 40 > sm


def test_ttm_rejects_non_contiguous_quarters():
    qs = make_quarters(8)
    qs[-2]["period_start"] = "2024-01-01"  # tear a hole in the sequence
    qs[-2]["period_end"] = "2024-03-28"
    assert metrics.build_ttm(qs) is None


def test_ttm_eps_fallback_guard_rejects_misscaled_shares():
    qs = make_quarters(8)
    for q in qs:
        del q["fields"]["diluted_eps"]
        q["fields"]["weighted_average_diluted_shares"] = 718.0  # millions bug
        q["fields"]["net_income"] = 8.7e9
    ttm = metrics.build_ttm(qs)
    assert "diluted_eps" not in ttm["fields"]  # guard: no absurd EPS stored


def test_bank_metrics_ttm_consistency():
    """Bank flow fields must sum across the TTM window like other flows."""
    qs = make_quarters(8)
    for i, q in enumerate(qs):
        q["fields"]["net_interest_income"] = 100.0 + i
        q["fields"]["noninterest_expense"] = 50.0
        q["fields"]["total_assets"] = 5000.0
        q["fields"]["total_deposits"] = 3000.0 + i * 100
    ttm = metrics.build_ttm(qs)
    assert ttm["fields"]["net_interest_income"] == sum(100.0 + i for i in range(4, 8))
    bm = metrics.bank_metrics(ttm, qs)
    assert bm["net_interest_income"] == ttm["fields"]["net_interest_income"]
    assert bm["total_deposits"] == qs[-1]["fields"]["total_deposits"]
    assert bm["efficiency_ratio_pct"] is not None
