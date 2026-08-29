from stock_machine.alpha_calibration import _realized, reliability


def test_reliability_reports_brier_and_bucket_gap():
    rows = [
        {"prob_outperform": 0.8, "actual_outperform": True},
        {"prob_outperform": 0.7, "actual_outperform": True},
        {"prob_outperform": 0.2, "actual_outperform": False},
        {"prob_outperform": 0.3, "actual_outperform": False},
    ]
    result = reliability(rows)
    assert result["status"] == "OK"
    assert result["n"] == 4
    assert 0 <= result["brier_score"] <= 1
    assert 0 <= result["expected_calibration_error"] <= 1
    assert result["bins"]


def test_realized_excess_uses_exact_future_trading_row():
    aligned = [
        ("2026-01-01", 100.0, 100.0),
        ("2026-01-02", 102.0, 101.0),
        ("2026-01-05", 110.0, 105.0),
    ]
    target = _realized(aligned, "2026-01-01", 2)
    assert target is not None
    target_date, excess = target
    assert target_date == "2026-01-05"
    assert excess > 0


def test_reliability_abstains_without_outcomes():
    assert reliability([])["status"] == "INSUFFICIENT_DATA"
