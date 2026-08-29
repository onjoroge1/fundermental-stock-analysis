from __future__ import annotations

from stock_machine.backtest.unified_model import FEATURES, walk_forward


def _panel():
    rows = []
    dates = [f"{year}-01-15" for year in range(2010, 2027)]
    for di, as_of in enumerate(dates):
        for i in range(10):
            # Construct a deterministic cross section where revisions and
            # fundamentals jointly rank future excess returns.
            signal = i - 4.5
            rows.append({
                "as_of": as_of,
                "ticker": f"T{i}",
                "composite": 50.0 + signal,
                "components": {
                    "growth": 50.0 + 2 * signal,
                    "profitability": 50.0 + signal,
                    "earnings_quality": 50.0 + 0.5 * signal,
                    "financial_health": 50.0,
                    "capital_allocation": 50.0,
                    "valuation": 50.0 - signal,
                },
                "factors": {
                    "earnings_yield_pct": 5.0 + 0.1 * signal,
                    "fcf_yield_pct": 4.0 + 0.1 * signal,
                    "revenue_yoy_pct": 10.0 + signal,
                    "roic_pct": 12.0 + 0.5 * signal,
                    "momentum_12m_pct": 5.0 + 0.2 * signal,
                },
                "expectations": {
                    "eps_revision_pct": 2.0 * signal,
                    "revenue_revision_pct": 1.5 * signal,
                    "latest_eps_surprise_pct": signal,
                    "trailing_4q_eps_surprise_pct": 0.8 * signal,
                },
                "forward": {"fwd_12m_pct": 3.0 * signal + 0.01 * di},
            })
    return rows


def test_unified_feature_contract_contains_expectations():
    names = {f"{top}.{sub}" for top, sub in FEATURES}
    assert "expectations.eps_revision_pct" in names
    assert "expectations.revenue_revision_pct" in names
    assert "expectations.latest_eps_surprise_pct" in names


def test_unified_walk_forward_uses_embargo_and_baseline_gate():
    result = walk_forward(_panel())
    assert result["status"] == "OK"
    assert result["protocol"]["embargo_days"] == 370
    assert result["test_dates"] > 0
    assert result["verdict"]["best_baseline"] in {
        "revenue_yoy", "momentum_12m", "composite"
    }
    assert isinstance(result["verdict"]["model_beats_baseline"], bool)
    assert "expectations.eps_revision_pct" in result["feature_weights_final"]
