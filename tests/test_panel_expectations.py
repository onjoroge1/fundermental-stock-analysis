from stock_machine.backtest.panel_expectations import expectations_as_of


def test_expectations_as_of_never_uses_future_vintages():
    consensus = [
        {"snapshot_date": "2026-01-01", "period_type": "quarter",
         "forecast_period_end": "2026-03-31", "eps_mean": 1.0,
         "revenue_mean": 100.0},
        {"snapshot_date": "2026-02-01", "period_type": "quarter",
         "forecast_period_end": "2026-03-31", "eps_mean": 1.1,
         "revenue_mean": 105.0},
        {"snapshot_date": "2026-03-01", "period_type": "quarter",
         "forecast_period_end": "2026-06-30", "eps_mean": 9.0,
         "revenue_mean": 900.0},
    ]
    surprises = [
        {"date": "2026-01-20", "surprise_pct": 8.0},
        {"date": "2026-03-20", "surprise_pct": 80.0},
    ]
    result = expectations_as_of(consensus, surprises, "2026-02-15")
    assert round(result["eps_revision_pct"], 3) == 10.0
    assert round(result["revenue_revision_pct"], 3) == 5.0
    assert result["latest_eps_surprise_pct"] == 8.0
    assert result["trailing_4q_eps_surprise_pct"] == 8.0
