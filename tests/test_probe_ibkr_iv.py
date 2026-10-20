"""IBKR volatility-history probe: parsing, coverage, resume, errors (no TWS needed)."""

import importlib.util
import json
from pathlib import Path

from stock_machine.market_calendar import session_dates

spec = importlib.util.spec_from_file_location(
    "probe_ibkr_iv_history", Path(__file__).resolve().parents[1] / "scripts" / "probe_ibkr_iv_history.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class FakeSession:
    def __init__(self, history):
        self.history, self.calls = history, []

    def head_timestamp(self, symbol, what):
        self.calls.append(("head", symbol, what))
        rows = self.history.get((symbol, what))
        return (rows[0]["date"] if rows else None), ([] if rows else [(162, "HMDS query returned no data")])

    def daily_bars(self, symbol, what, duration):
        self.calls.append(("bars", symbol, what, duration))
        return list(self.history.get((symbol, what), [])), []


def bars(days, level=0.3):
    return [{"date": d, "open": level, "high": level * 1.1, "low": level * 0.9, "close": level} for d in days]


def test_dates_and_head_timestamps_parse():
    assert probe.parse_bar_date("20160104") == "2016-01-04"
    assert probe.parse_head("20160104-14:30:00") == "2016-01-04"
    assert probe.parse_head("20160104  14:30:00") == "2016-01-04" and probe.parse_head(None) is None


def test_coverage_measures_sessions_and_gaps():
    days = session_dates("2024-01-02", "2024-03-28")
    missing = set(days[10:14])
    out = probe.coverage(bars([d for d in days if d not in missing]), "2024-01-02", session_dates)
    assert out["first"] == "2024-01-02" and out["last"] == "2024-03-28"
    assert out["longest_gap_sessions"] == 4 and out["session_coverage"] == round((len(days) - 4) / len(days), 4)
    assert probe.coverage([], None, session_dates)["bars"] == 0


def test_the_probe_writes_labelled_files_resumes_and_records_errors(tmp_path):
    days = session_dates("2016-01-04", "2016-02-26")
    history = {("AAPL", "OPTION_IMPLIED_VOLATILITY"): bars(days), ("AAPL", "HISTORICAL_VOLATILITY"): bars(days, 0.2)}
    session = FakeSession(history)
    out_dir = tmp_path / "2026-10-20"
    summary = probe.run(session, ["AAPL", "ZZZZ"], out_dir, duration="15 Y", force=False,
                        sessions_between=session_dates, log=lambda *_: None)
    saved = json.loads((out_dir / "AAPL.json").read_text())
    assert saved["provenance"].startswith("IBKR TWS reqHistoricalData") and "not observed option quotes" in saved["provenance"]
    assert saved["series"]["OPTION_IMPLIED_VOLATILITY"]["coverage"]["first"] == "2016-01-04"
    assert summary["coverage"]["ZZZZ"]["OPTION_IMPLIED_VOLATILITY"]["bars"] == 0
    assert "162" in summary["coverage"]["ZZZZ"]["errors"]
    md = (out_dir / "coverage.md").read_text()
    assert "| AAPL | 2016-01-04 | 2016-01-04 |" in md and "1 of 2 symbols returned bars" in md
    assert saved["duration"] == "15 Y"
    again = FakeSession(history)
    probe.run(again, ["AAPL", "ZZZZ"], out_dir, duration="15 Y", force=False, sessions_between=session_dates,
              log=lambda *_: None)
    assert {c[1] for c in again.calls} == {"ZZZZ"}             # complete AAPL kept; the empty symbol retried
    longer = FakeSession(history)
    probe.run(longer, ["AAPL"], out_dir, duration="21 Y", force=False, sessions_between=session_dates,
              log=lambda *_: None)
    assert {c[1] for c in longer.calls} == {"AAPL"}            # a different duration is fetched again
    assert {c[2] for c in session.calls} == set(probe.SERIES)


def test_a_timed_out_series_is_not_treated_as_complete():
    ok = {"duration": "21 Y", "series": {w: {"coverage": {"bars": 10}, "errors": []} for w in probe.SERIES}}
    assert probe.complete(ok, "21 Y") and not probe.complete(ok, "15 Y")
    timed_out = {**ok, "series": {**ok["series"], "HISTORICAL_VOLATILITY": {"coverage": {"bars": 10},
                                                                            "errors": [["TIMEOUT", "no answer"]]}}}
    assert not probe.complete(timed_out, "21 Y")
    legacy = {"series": ok["series"]}                          # files written before the duration was recorded
    assert probe.complete(legacy, "21 Y")


def test_the_probe_is_read_only_and_local_by_default():
    text = (Path(__file__).resolve().parents[1] / "scripts" / "probe_ibkr_iv_history.py").read_text()
    for forbidden in ("placeOrder", "cancelOrder", "reqGlobalCancel", "exerciseOptions", "db.connect"):
        assert forbidden not in text
    assert "127.0.0.1" in probe.LOCAL_HOSTS and "--allow-remote" in text
