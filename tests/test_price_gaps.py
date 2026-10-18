"""An incomplete latest bar is a retryable gap, never a stored price; isolated
technical gaps are reported without failing the scheduled job."""

import pytest

from stock_machine import pipeline
from stock_machine.data_quality import complete_bar
from stock_machine.agent_intelligence import technical_setup_store as store

GOOD = {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "adj_close": 10.4, "volume": 1000}


@pytest.mark.parametrize("change", [
    {"volume": None}, {"open": None}, {"high": 10.0}, {"low": 10.6}, {"close": 0.0},
    {"adj_close": float("nan")}, {"volume": True}, {"volume": -1},
])
def test_incomplete_bars(change):
    assert complete_bar(GOOD)
    assert not complete_bar({**GOOD, **change})


def test_incomplete_latest_bar_is_rejected_as_retryable(monkeypatch):
    monkeypatch.setattr(pipeline, "latest_completed_session", lambda: "2026-10-16")
    rows = [{"date": "2026-10-15", **GOOD}, {"date": "2026-10-16", **GOOD, "volume": None}]
    with pytest.raises(ValueError, match="PRICE_LATEST_BAR_INCOMPLETE") as err:
        pipeline._completed_prices(rows)
    assert pipeline.is_retryable_price_error(err.value)


def test_complete_latest_bar_passes(monkeypatch):
    monkeypatch.setattr(pipeline, "latest_completed_session", lambda: "2026-10-16")
    monkeypatch.setattr(pipeline, "assess_dataset", lambda name, rows: {"status": "PASS"})
    rows = [{"date": "2026-10-15", **GOOD}, {"date": "2026-10-16", **GOOD}]
    assert pipeline._completed_prices(rows) == rows


def test_isolated_gaps_are_not_systemic():
    assert store.attention_summary(["DELL"]) == {
        "gap_tickers": ["DELL"], "systemic": False,
        "max_isolated_gap_tickers": store.MAX_ISOLATED_GAP_TICKERS}
    assert store.attention_summary(["A", "B", "C", "D"])["systemic"] is True


@pytest.mark.parametrize("receipt,code", [
    ({"status": "OK"}, 0),
    ({"status": "SKIPPED"}, 0),
    ({"status": "ATTENTION", "attention": store.attention_summary(["DELL"])}, 0),
    ({"status": "ATTENTION", "attention": store.attention_summary(list("ABCD"))}, 1),
    ({"status": "FAILED"}, 1),
    ({"status": "BUSY"}, 1),
])
def test_job_fails_only_on_systemic_gaps_or_errors(monkeypatch, tmp_path, receipt, code):
    from scripts import run_technical_setups as job

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(job, "run_daily", lambda: {"as_of": "2026-10-16", **receipt})
    assert job.main() == code
