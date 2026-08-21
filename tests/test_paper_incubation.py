from datetime import date, timedelta

from stock_machine.paper_incubation import evaluate_marks


def _marks(count=40, strategy_step=0.10, benchmark_step=0.05):
    start = date(2026, 1, 1)
    return [{
        "date": (start + timedelta(days=i * 4)).isoformat(),
        "status": "OK",
        "net_return_pct": i * strategy_step,
        "benchmark_return_pct": i * benchmark_step,
        "excess_return_pct": i * (strategy_step - benchmark_step),
        "coverage": 1.0,
    } for i in range(count)]


def test_forward_incubation_requires_time_and_mark_count():
    result = evaluate_marks("2026-01-01", _marks(20))
    assert result["status"] == "COLLECTING"
    assert result["gates"]["minimum_marks"] is False


def test_forward_incubation_can_only_become_review_eligible():
    result = evaluate_marks("2026-01-01", _marks())
    assert result["status"] == "REVIEW_ELIGIBLE"
    assert result["execution_status"] == "PAPER_ONLY"
    assert all(result["gates"].values())
    assert "never authorizes live capital" in result["principle"]


def test_mature_underperforming_cohort_fails_precommitted_gates():
    result = evaluate_marks(
        "2026-01-01", _marks(strategy_step=0.02, benchmark_step=0.08),
    )
    assert result["status"] == "FAILED"
    assert result["gates"]["positive_cumulative_excess"] is False
    assert result["gates"]["daily_excess_hit_rate"] is False


def test_incomplete_marks_are_not_counted_as_evidence():
    marks = _marks()
    for row in marks[5:]:
        row["status"] = "BLOCKED"
    result = evaluate_marks("2026-01-01", marks)
    assert result["status"] == "COLLECTING"
    assert result["marks"] == 5


def test_total_loss_fails_instead_of_dividing_by_zero():
    marks = _marks()
    for row in marks[10:]:
        row["net_return_pct"] = -100.0
        row["excess_return_pct"] = -105.0
    result = evaluate_marks("2026-01-01", marks)
    assert result["status"] == "FAILED"
    assert result["max_drawdown_pct"] == -100.0
