"""Intraday replay reports: previews over finished months and the written summary of a stage (no IBKR needed)."""

import importlib.util
from pathlib import Path

from stock_machine.backtest import intraday_replay as ir

spec = importlib.util.spec_from_file_location(
    "report_intraday_replay", Path(__file__).resolve().parents[1] / "scripts" / "report_intraday_replay.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def stage(result, name, months):
    return {"protocol_id": ir.PROTOCOL_ID, "protocol_sha256": ir.PROTOCOL_SHA256, "behavior_sha256": ir.BEHAVIOR_SHA256,
            **ir.stage_report(result, name, months), "generated_at": "2026-10-22T00:00:00+00:00",
            "inputs": {"prices_sha256": ir.DAILY_PRICES_SHA256, "earnings_sha256": "e" * 64, "bars_sha256": "b" * 64}}


def test_a_development_summary_lists_every_strategy_its_claims_and_exclusions_without_verdicts():
    text = report.render(stage(ir.behavior_run(), "dev", ir.BEHAVIOR_MONTHS))
    assert "development descriptive, 2019-02..2019-03" in text and "Development numbers are descriptive" in text
    for name in (*ir.CONFIRMATORY, *ir.EXPLORATORY):
        assert f"| **{name}** |" in text
    assert "Verdict" not in text and "1–3% gaps reach the prior close by 10:30" in text and "CORPORATE_ACTION" in text
    assert text.index("| **G1** |") < text.index("| **X1** |") < text.index("| **E1** |")   # protocol order
    assert "BEFORE_FIRST_DAILY_BAR" in text and "T6 (BEFORE" not in text           # pre-history is not "heavy"
    assert "T4 (CORPORATE_ACTION 1, PRICE_MISMATCH 1)" in text      # 2 of 40 sessions: at the 5% line


def test_a_holdout_summary_carries_the_verdicts():
    book = ir.synthetic_book()
    text = report.render(stage(book, "holdout", ("2024-01", "2025-12")))
    assert "intraday holdout pseudo out of sample" in text and "| Verdict |" in text and "SUPPORTED" in text
    assert "Development numbers are descriptive" not in text
    assert "ROBUST, COST_SENSITIVE" in text and "| **A1** | Attention gap fade | exploratory | 0 | 0 | 0/24 | – | no trades |" in text
    assert "| 240 | 20 | 24/24 | 5% |" in text and "Warning" not in text


def write_finished(root, symbols, months):
    for t in symbols:
        for m in months:
            folder = Path(root) / t
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{m}.json").write_text("{}")
            (folder / f"{m}.csv.gz").write_bytes(b"")


def test_a_preview_covers_the_unbroken_run_of_finished_months_after_the_warm_up(tmp_path):
    symbols = ["AAA", "SPY"]
    assert report.finished_through(tmp_path, symbols, "2016-01", "2016-12") is None
    write_finished(tmp_path, symbols, ["2015-12"])
    assert report.finished_through(tmp_path, symbols, "2016-01", "2016-12") is None       # warm-up only
    write_finished(tmp_path, symbols, ["2016-01", "2016-02", "2016-04"])
    assert report.finished_through(tmp_path, symbols, "2016-01", "2016-12") == "2016-02"  # 2016-03 breaks the run
    (tmp_path / "AAA" / "2016-02.partial.json").write_text("{}")
    assert report.finished_through(tmp_path, symbols, "2016-01", "2016-12") == "2016-01"
    assert report.preview(tmp_path / "none", tmp_path)["reason"] == "NO_FINISHED_MONTH"


def test_losing_books_show_no_labels_coverage_gaps_are_named_and_stale_reports_are_flagged():
    report_ = stage(ir.synthetic_book(reward=-0.5), "holdout", ("2024-01", "2025-12"))
    text = report.render(report_)
    assert "NOT_SUPPORTED" in text and "FRAGILE" not in text.split("## Results")[1].split("## Barak")[0]
    report_["excluded_by_ticker"] = {"UAL": {"BEFORE_FIRST_DAILY_BAR": 200}, "ABNB": {"BEFORE_FIRST_DAILY_BAR": 240}}
    report_["behavior_sha256"] = "0" * 64
    text = report.render(report_)
    assert "UAL 200 (listed throughout" in text and "ABNB 240;" in text
    assert "Warning: this report was produced by a different replay code" in text


def test_the_console_summary_prints_small_bounds_and_cost_sensitivity_legibly():
    spec_ = importlib.util.spec_from_file_location(
        "run_intraday_replay", Path(__file__).resolve().parents[1] / "scripts" / "run_intraday_replay.py")
    runner = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(runner)
    text = runner.summary(stage(ir.synthetic_book(reward=0.0215, skill=0.0215), "holdout", ("2024-01", "2025-12")))
    g1 = next(line for line in text.splitlines() if line.startswith("G1 "))
    assert "e-" in g1 and "SUPPORTED" in g1 and "COST_SENSITIVE" in g1
    assert "no trades" in next(line for line in text.splitlines() if line.startswith("A1 "))
