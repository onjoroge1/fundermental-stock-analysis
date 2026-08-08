import math

from stock_machine.backtest.engine import quarterly_grid
from stock_machine.backtest.evaluate import (_quantile_means, evaluate,
                                             spearman)
from stock_machine.outcomes import score_report


def test_spearman_perfect_and_inverse():
    assert math.isclose(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
    assert math.isclose(spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)


def test_spearman_handles_ties():
    r = spearman([1, 2, 2, 3], [1, 2, 3, 4])
    assert r is not None and 0 < r < 1


def test_quantile_means_ordering():
    # factor perfectly predicts return: top quintile mean must beat bottom
    pairs = [(float(i), float(i)) for i in range(20)]
    qm = _quantile_means(pairs)
    assert qm[0] > qm[-1]
    assert math.isclose(qm[0], sum(range(16, 20)) / 4)


def test_quarterly_grid():
    grid = quarterly_grid("2024-01-01", "2024-12-31")
    assert grid == ["2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01"]


def _obs(as_of, ticker, comp, fwd):
    return {"as_of": as_of, "ticker": ticker, "sector": "X",
            "composite": comp, "components": {"valuation": comp,
                                              "growth": comp},
            "factors": {"earnings_yield_pct": comp % 3, "fcf_yield_pct": None,
                        "revenue_yoy_pct": -comp, "roic_pct": None,
                        "momentum_12m_pct": None},
            "forward": {"fwd_12m_pct": fwd}}


def test_evaluate_ic_signs():
    # composite ranks exactly with forward returns on both dates;
    # revenue_yoy factor is anti-correlated by construction
    obs = []
    for d in ("2020-01-01", "2020-04-01"):
        for i in range(10):
            obs.append(_obs(d, f"T{i}", float(i), float(i * 2)))
    out = evaluate(obs, "fwd_12m_pct")
    assert out["dates_used"] == 2
    assert math.isclose(out["factors"]["composite_score"]["mean_ic"], 1.0)
    assert math.isclose(out["factors"]["revenue_yoy"]["mean_ic"], -1.0)
    assert out["verdict"]["composite_beats_baselines"] is True
    assert out["factors"]["composite_score"]["top_minus_bottom_pct"] > 0


def _report(as_of="2025-01-01"):
    return {
        "as_of": f"{as_of}T12:00:00-04:00",
        "conclusion": {"classification": "WATCH"},
        "forecasts": {
            "three_month": {"expected_return_pct": 5.0,
                            "fair_value_low": 90.0, "fair_value_high": 130.0},
            "twelve_month": {"expected_return_pct": 20.0,
                             "fair_value_low": 80.0, "fair_value_high": 150.0},
        },
    }


def _prices(last="2025-06-01"):
    # flat 100 until 2025-03-01, then 110 (both close and adj_close)
    from datetime import date, timedelta
    rows = []
    d = date(2024, 12, 1)
    while d.isoformat() <= last:
        px = 100.0 if d < date(2025, 3, 1) else 110.0
        rows.append({"date": d.isoformat(), "close": px, "adj_close": px})
        d += timedelta(days=1)
    return rows


def test_outcome_scorer_scores_only_due_horizons():
    rows = score_report(_report(), _prices(), today="2025-06-01")
    horizons = {r["horizon"] for r in rows}
    assert horizons == {"three_month"}  # 12m not due until 2026-01
    r = rows[0]
    assert math.isclose(r["actual_return_pct"], 10.0)
    assert math.isclose(r["error_pct"], 5.0)
    assert r["in_range"] is True
    assert r["direction_hit"] is True


def test_outcome_scorer_direction_miss_and_range():
    rep = _report()
    rep["forecasts"]["three_month"]["expected_return_pct"] = -5.0
    rep["forecasts"]["three_month"]["fair_value_high"] = 105.0
    rows = score_report(rep, _prices(), today="2025-06-01")
    r = rows[0]
    assert r["direction_hit"] is False
    assert r["in_range"] is False  # actual 110 > high 105


def test_ridge_recovers_linear_signal_walk_forward():
    """Synthetic panel where the valuation component linearly drives excess
    return: walk-forward ridge must find it (positive IC) while respecting
    the embargo (early dates produce no prediction)."""
    from stock_machine.backtest.model import walk_forward
    obs = []
    dates = [f"20{y:02d}-0{q}-01" for y in range(14, 24) for q in (1, 4, 7)]
    for d in dates:
        for i in range(12):
            signal = float(i)
            obs.append({
                "as_of": d, "ticker": f"T{i}", "sector": "X",
                "composite": 50.0,  # deliberately uninformative
                "components": {"growth": 1.0, "profitability": 1.0,
                               "earnings_quality": 1.0,
                               "financial_health": 1.0,
                               "capital_allocation": 1.0,
                               "valuation": signal},
                "factors": {"earnings_yield_pct": 3.0, "fcf_yield_pct": 2.0,
                            "revenue_yoy_pct": 10.0, "roic_pct": 15.0,
                            "momentum_12m_pct": None},
                "forward": {"fwd_12m_pct": signal * 4.0},
            })
    out = walk_forward(obs)
    assert out["status"] == "OK"
    assert out["ml_mean_ic"] > 0.9
    assert out["test_dates"] < len(dates)  # embargo consumed early dates
    weights = out["feature_weights_final"]
    assert weights["components.valuation"] > abs(weights["components.growth"])


def test_ridge_solver():
    from stock_machine.backtest.model import _solve
    # x + 2y = 5; 3x - y = 1  ->  x=1, y=2
    x = _solve([[1.0, 2.0], [3.0, -1.0]], [5.0, 1.0])
    assert abs(x[0] - 1.0) < 1e-9 and abs(x[1] - 2.0) < 1e-9
