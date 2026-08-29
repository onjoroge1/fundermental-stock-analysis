from stock_machine.backtest.shadow import evaluate_shadow, panel_coverage


def _row(as_of, ticker, with_expectations=True):
    signal = int(ticker[1:]) - 4
    expectations = ({
        "eps_revision_pct": float(signal),
        "revenue_revision_pct": float(signal),
        "latest_eps_surprise_pct": float(signal),
        "trailing_4q_eps_surprise_pct": float(signal),
    } if with_expectations else {
        "eps_revision_pct": None,
        "revenue_revision_pct": None,
        "latest_eps_surprise_pct": None,
        "trailing_4q_eps_surprise_pct": None,
    })
    return {
        "as_of": as_of,
        "ticker": ticker,
        "composite": 50 + signal,
        "components": {
            "growth": 50 + signal,
            "profitability": 50 + signal,
            "earnings_quality": 50,
            "financial_health": 50,
            "capital_allocation": 50,
            "valuation": 50 - signal,
        },
        "factors": {
            "earnings_yield_pct": 5 + signal * 0.1,
            "fcf_yield_pct": 4 + signal * 0.1,
            "revenue_yoy_pct": 10 + signal,
            "roic_pct": 12 + signal * 0.2,
            "momentum_12m_pct": signal * 2,
        },
        "expectations": expectations,
        "forward": {"fwd_12m_pct": signal * 3.0},
    }


def test_panel_coverage_counts_only_real_expectations():
    rows = [_row("2025-01-01", "T1", True),
            _row("2025-01-01", "T2", False)]
    c = panel_coverage(rows)
    assert c["observations"] == 2
    assert c["expectations_observations"] == 1
    assert c["expectations_coverage"] == 0.5
    assert c["expectations_dates"] == 1


def test_shadow_fails_closed_when_expectations_history_is_sparse():
    rows = []
    for year in range(2010, 2027):
        for i in range(10):
            rows.append(_row(f"{year}-01-01", f"T{i}", year >= 2026))
    result = evaluate_shadow(rows)
    assert result["promotion"]["coverage_gate"] is False
    assert result["promotion"]["deployed_as_primary"] is False
    assert result["promotion"]["decision"] == "PENDING_MORE_POINT_IN_TIME_EXPECTATIONS_HISTORY"
