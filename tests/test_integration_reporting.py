import pytest
from stock_machine.integrations.reporting import performance_series, render_report


def rows(values):
    return [{"session": f"2020-01-{i+2:02d}", "equity_usd": str(v), "external_flows_usd": "1000", "status": "COMPLETE"} for i, v in enumerate(values)]


def test_inception_fees_and_compounding_reconcile():
    data = rows([999, 1010, 990])
    result = performance_series(data, [d["session"] for d in data])
    assert result["metrics"]["total_return"] == pytest.approx(-.01)
    assert result["returns"][0] == pytest.approx(-.001)
    assert result["metrics"]["max_drawdown"] == pytest.approx(990/1010-1)
    assert result["metrics"]["sharpe_rf_zero"] is None


def test_later_deposit_cannot_appear_as_investment_return():
    data = rows([1000, 2000])
    data[1]["external_flows_usd"] = "2000"
    result = performance_series(data, [r["session"] for r in data])
    assert result["status"] == "WITHHELD" and result["returns"] == []
    assert "CASHFLOW" in result["blockers"][0]


def test_missing_marks_and_gaps_never_become_zero_returns():
    data = rows([1000, 1010])
    data[1]["status"], data[1]["equity_usd"] = "WITHHELD", None
    result = performance_series(data, [r["session"] for r in data])
    assert result["returns"] == []
    assert result["points"][-1] == {"time": "2020-01-03"}
    assert performance_series(rows([1000]), ["2020-01-02", "2020-01-03"])["status"] == "WITHHELD"


def test_zero_history_and_bad_numbers():
    assert performance_series([], [])["status"] == "NO_HISTORY"
    for value in ("NaN", "Infinity", True):
        with pytest.raises(ValueError):
            performance_series(rows([value]), ["2020-01-02"])


def test_report_is_inert_and_benchmark_requires_exact_dates():
    data = rows([999, 1010])
    snapshot = {"schema_version": "performance-input.v1", "portfolio_id": "<script>bad</script>",
                "quality": "LEGACY_SIMULATION", "valuations": data,
                "sessions": [r["session"] for r in data], "benchmark": []}
    result = render_report(snapshot, with_quantstats=False)
    assert "<script>" not in result["html"]
    assert result["benchmark"]["status"] == "WITHHELD"
    snapshot["benchmark"] = [{"date": "2020-01-02", "adj_close": "100"}, {"date": "2020-01-03", "adj_close": "110"}]
    result = render_report(snapshot, with_quantstats=False)
    assert result["benchmark"]["points"][-1]["value"] == 1100
