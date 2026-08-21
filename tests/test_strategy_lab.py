from stock_machine.strategy_lab import run


def _panel():
    rows = []
    dates = [f"20{year:02d}-{month:02d}-01"
             for year in range(18, 24) for month in (1, 4, 7, 10)]
    for date_index, as_of in enumerate(dates):
        for i in range(12):
            signal = (i + date_index) % 12
            noise = 4 if signal % 2 == 0 else -4
            rows.append({
                "as_of": as_of,
                "ticker": f"T{i:02d}",
                "composite": float((i * 7) % 12),
                "components": {"profitability": float(signal - noise)},
                "factors": {
                    "earnings_yield_pct": float(signal + noise),
                    "roic_pct": float(signal - noise),
                    "revenue_yoy_pct": float((i * 5) % 12),
                    "momentum_12m_pct": float(11 - signal),
                },
                "forward": {"fwd_3m_pct": float(signal - 2)},
            })
    return rows


def test_strategy_lab_uses_untouched_chronological_evaluation_window():
    result = run(_panel())
    split = result["date_split"]
    assert result["status"] == "OK"
    assert split["development_end"] < split["evaluation_start"]
    assert split["development_periods"] == 14
    assert split["evaluation_periods"] == 10


def test_multifactor_must_beat_best_single_factor_after_costs():
    result = run(_panel())
    value_quality = result["strategies"]["value_quality"]
    assert result["best_single_factor"] == "earnings_yield"
    assert (value_quality["evaluation"]["annualized_excess_pct"]
            > result["best_single_factor_excess_pct"])
    assert value_quality["promotion"]["status"] == "PAPER_ELIGIBLE"
    assert all(value_quality["promotion"]["gates"].values())


def test_turnover_cost_reduces_evaluation_return():
    free = run(_panel(), cost_bps=0)
    costly = run(_panel(), cost_bps=100)
    free_return = free["strategies"]["value_quality"]["evaluation"][
        "annualized_return_pct"]
    costly_return = costly["strategies"]["value_quality"]["evaluation"][
        "annualized_return_pct"]
    assert costly_return < free_return


def test_strategy_lab_abstains_without_enough_history():
    result = run(_panel()[:48])  # only four complete cross-sections
    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert "12 quarterly" in result["reason"]


def test_negative_cost_is_rejected():
    try:
        run(_panel(), cost_bps=-1)
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("negative transaction cost must fail")
