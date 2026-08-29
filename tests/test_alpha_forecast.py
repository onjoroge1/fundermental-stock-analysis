from __future__ import annotations

from datetime import date, timedelta
from math import exp

from stock_machine.alpha_forecast import (
    DIRECT_HORIZONS,
    align_prices,
    expectation_features,
    excess_log_returns,
    forecast_alpha,
)


def _prices(n: int = 1300):
    start = date(2021, 1, 1)
    benchmark = 100.0
    stock = 100.0
    stock_rows = []
    benchmark_rows = []
    for i in range(n):
        d = (start + timedelta(days=i)).isoformat()
        # Deterministic cyclical relative momentum gives the direct model a
        # learnable but nontrivial signal while keeping prices positive.
        market_r = 0.0002 + (0.0003 if (i // 40) % 2 == 0 else -0.0002)
        alpha_r = 0.0008 if (i // 25) % 2 == 0 else -0.0005
        benchmark *= exp(market_r)
        stock *= exp(market_r + alpha_r)
        benchmark_rows.append({"date": d, "adj_close": benchmark})
        stock_rows.append({"date": d, "adj_close": stock})
    return stock_rows, benchmark_rows


def test_align_and_excess_return_target():
    stock = [
        {"date": "2026-01-01", "adj_close": 100.0},
        {"date": "2026-01-02", "adj_close": 110.0},
    ]
    benchmark = [
        {"date": "2026-01-01", "adj_close": 100.0},
        {"date": "2026-01-02", "adj_close": 105.0},
    ]
    aligned = align_prices(stock, benchmark)
    excess = excess_log_returns(aligned)
    assert len(excess) == 1
    assert excess[0] > 0


def test_expectation_features_are_point_in_time():
    consensus = [
        {"snapshot_date": "2026-01-01", "period_type": "quarter",
         "forecast_period_end": "2026-03-31", "eps_mean": 1.0,
         "revenue_mean": 100.0},
        {"snapshot_date": "2026-02-01", "period_type": "quarter",
         "forecast_period_end": "2026-03-31", "eps_mean": 1.2,
         "revenue_mean": 110.0},
        # Must not leak into a 2026-02-15 observation.
        {"snapshot_date": "2026-03-01", "period_type": "quarter",
         "forecast_period_end": "2026-06-30", "eps_mean": 9.0,
         "revenue_mean": 900.0},
    ]
    surprises = [
        {"date": "2026-01-20", "surprise_pct": 10.0},
        {"date": "2026-03-20", "surprise_pct": 90.0},
    ]
    f = expectation_features("2026-02-15", consensus, surprises)
    assert round(f[0], 3) == 0.2
    assert round(f[1], 3) == 0.1
    assert round(f[2], 3) == 0.1
    assert round(f[3], 3) == 0.1
    assert f[4:] == [1.0, 1.0]


def test_forecast_uses_distinct_direct_horizons():
    stock, benchmark = _prices()
    result = forecast_alpha("TEST", stock, "SPY", benchmark)
    assert result["status"] == "OK"
    assert result["direct_horizons"] == list(DIRECT_HORIZONS)
    assert result["target"] == "cumulative stock-minus-benchmark log return"
    assert result["promotion"]["deployed_as_primary"] is False
    assert set(result["horizons"]) == {str(h) for h in DIRECT_HORIZONS}
    for horizon in DIRECT_HORIZONS:
        row = result["horizons"][str(horizon)]
        assert "validation" in row
        if row["status"] == "OK":
            assert 0.0 <= row["prob_outperform"] <= 1.0
            assert row["training_rows"] >= 120


def test_forecast_abstains_without_aligned_benchmark_history():
    stock, benchmark = _prices(300)
    result = forecast_alpha("TEST", stock, "SPY", benchmark)
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["benchmark"] == "SPY"
