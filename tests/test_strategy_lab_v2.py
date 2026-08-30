from stock_machine.strategy_lab_v2 import (
    MAX_SECTOR_SHARE_PER_LEG,
    STRATEGIES,
    _select,
    run,
    score_policy_rows,
)


def _row(ticker: str, sector: str, as_of: str, signal: float, future: float):
    return {
        "ticker": ticker,
        "sector": sector,
        "as_of": as_of,
        "composite": signal * 10 + 50,
        "components": {
            "growth": signal * 10 + 50,
            "profitability": signal * 10 + 50,
            "earnings_quality": signal * 10 + 50,
            "financial_health": 60,
            "capital_allocation": 60,
            "valuation": signal * 10 + 50,
        },
        "factors": {
            "earnings_yield_pct": signal,
            "fcf_yield_pct": signal,
            "revenue_yoy_pct": signal,
            "roic_pct": signal,
            "momentum_12m_pct": signal,
        },
        "expectations": {
            "eps_revision_pct": signal,
            "revenue_revision_pct": signal,
            "latest_eps_surprise_pct": signal,
            "trailing_4q_eps_surprise_pct": signal,
        },
        "forward": {"fwd_3m_pct": future},
    }


def _panel():
    panel = []
    # 20 quarterly cross-sections; signal monotonically predicts future return.
    for year in range(2020, 2025):
        for month in (1, 4, 7, 10):
            as_of = f"{year}-{month:02d}-01"
            for i in range(10):
                signal = float(i - 4.5)
                panel.append(_row(
                    f"T{i}", "Tech" if i < 5 else "Consumer",
                    as_of, signal, signal * 2.0,
                ))
    return panel


def test_score_policy_rows_is_deterministic_and_higher_is_better():
    rows = [
        _row("A", "Tech", "2026-01-01", 1, 1),
        _row("B", "Tech", "2026-01-01", 2, 2),
        _row("C", "Consumer", "2026-01-01", 3, 3),
        _row("D", "Consumer", "2026-01-01", 4, 4),
    ]
    result = score_policy_rows(rows, STRATEGIES["value_quality"])
    assert result is not None
    assert result["scores"]["D"] > result["scores"]["A"]


def test_sector_cap_prevents_one_sector_from_filling_leg():
    by_ticker = {
        f"T{i}": {"sector": "Tech" if i < 5 else "Consumer"}
        for i in range(10)
    }
    ranking = [f"T{i}" for i in range(10)]
    picked = _select(ranking, by_ticker, count=4,
                     max_sector_share=MAX_SECTOR_SHARE_PER_LEG)
    assert len(picked) == 4
    sectors = [by_ticker[t]["sector"] for t in picked]
    assert sectors.count("Tech") <= 2


def test_strategy_lab_v2_runs_long_only_and_long_short_without_backfill():
    result = run(_panel(), cost_bps=15)
    assert result["status"] == "OK"
    assert set(result["modes"]) == {"long_only", "long_short"}
    assert result["p2_current_policy"]["status"] == "FORWARD_ONLY_NOT_BACKFILLED"
    assert result["date_split"]["evaluation_periods"] >= 8
    assert result["modes"]["long_only"]["strategies"]["value_quality"]["evaluation"]["periods"] >= 8
    assert result["modes"]["long_short"]["strategies"]["value_quality"]["evaluation"]["periods"] >= 8


def test_long_short_policy_captures_cross_sectional_spread():
    result = run(_panel(), cost_bps=0)
    metrics = result["modes"]["long_short"]["strategies"]["value_quality"]["evaluation"]
    assert metrics["annualized_return_pct"] > 0
    assert metrics["positive_period_share"] == 1.0


def test_insufficient_history_fails_closed():
    result = run(_panel()[:80])  # only eight cross-sections
    assert result["status"] == "INSUFFICIENT_HISTORY"
