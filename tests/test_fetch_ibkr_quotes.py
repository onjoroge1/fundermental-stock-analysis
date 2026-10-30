"""IBKR quote fetcher for the cost study: plan checks, hold-out refusal, parsing, pacing, files, resume (no TWS needed)."""

import csv
import gzip
import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

spec = importlib.util.spec_from_file_location(
    "fetch_ibkr_quotes", Path(__file__).resolve().parents[1] / "scripts" / "fetch_ibkr_quotes.py")
fq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fq)
ET = ZoneInfo("America/New_York")


def plan(tmp_path, rows):
    p = tmp_path / "plan.csv"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fq.PLAN_COLS)
        w.writeheader()
        w.writerows(rows)
    return p


def row(session="2026-09-02", what="BID_ASK", bar="1 secs", ticker="AAPL", part="S"):
    return {"id": f"{part}-{ticker}-{session}-{what}", "part": part, "ticker": ticker, "session": session,
            "end": "10:00:00", "duration": "1800 S", "bar": bar, "what": what, "tag": "s"}


def test_the_hold_out_and_incomplete_sessions_are_refused_with_no_override(tmp_path):
    for s in ("2024-01-02", "2025-06-10", "2026-08-31"):
        with pytest.raises(SystemExit, match="hold-out"):
            fq.read_plan(plan(tmp_path, [row(session=s)]), today="2026-10-30")
    with pytest.raises(SystemExit, match="not a completed session"):
        fq.read_plan(plan(tmp_path, [row(session="2026-10-30")]), today="2026-10-30")
    assert len(fq.read_plan(plan(tmp_path, [row(session="2023-12-29", bar="1 min", part="M"), row()]),
                            today="2026-10-30")) == 2


def test_unknown_fields_and_duplicates_are_refused(tmp_path):
    with pytest.raises(SystemExit, match="bad field"):
        fq.read_plan(plan(tmp_path, [row(what="MIDPOINT")]), today="2026-10-30")
    with pytest.raises(SystemExit, match="bad field"):
        fq.read_plan(plan(tmp_path, [{**row(), "ticker": "aapl; rm"}]), today="2026-10-30")
    with pytest.raises(SystemExit, match="duplicate"):
        fq.read_plan(plan(tmp_path, [row(), row()]), today="2026-10-30")


def bar(clock, session, o, h, l, c, v=None):
    t = datetime.fromisoformat(f"{session}T{clock}").replace(tzinfo=ET)
    return SimpleNamespace(date=str(int(t.timestamp())), open=o, high=h, low=l, close=c, volume=v, wap=c, barCount=3)


def test_bid_ask_fields_follow_ibkr_definitions_and_other_sessions_are_dropped():
    r = row()
    got = fq.parse([bar("09:30:01", "2026-09-02", 99.98, 100.05, 99.95, 100.02),
                    bar("15:59:59", "2026-09-01", 1, 1, 1, 1)], r)
    assert got == [{"time": "09:30:01", "bid_avg": 99.98, "ask_max": 100.05, "bid_min": 99.95, "ask_avg": 100.02}]
    t = fq.parse([bar("09:30:00", "2026-09-02", 10, 11, 9, 10.5, v=-1)], row(what="TRADES"))
    assert t[0]["volume"] == 0.0 and t[0]["count"] == 3


def test_small_bars_stay_inside_60_per_10_minutes_with_bid_ask_counted_twice():
    clock = {"t": 0.0}
    pacer = fq.Pacer(0.0, clock=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    stamps = []
    for _ in range(40):                    # 40 BID_ASK = 80 units: the second half must wait for the window
        pacer.wait(2, True)
        stamps.append((clock["t"], 2))
    for t, _ in stamps:
        in_window = sum(w for s, w in stamps if t - fq.SMALL_WINDOW < s <= t)
        assert in_window <= fq.SMALL_BUDGET
    assert stamps[-1][0] >= fq.SMALL_WINDOW


class Fake:
    def __init__(self, throttle=0, empty=()):
        self.calls, self.throttle, self.empty = [], throttle, set(empty)

    def bars(self, r):
        self.calls.append(r["id"])
        if self.throttle:
            self.throttle -= 1
            return [], [(162, "Historical Market Data Service error message:API historical data query cancelled: pacing violation")]
        if r["session"] in self.empty:
            return [], [(162, "Historical Market Data Service error message:HMDS query returned no data")]
        return [{"time": "09:30:00", "bid_avg": 1.0, "ask_max": 1.02, "bid_min": 0.99, "ask_avg": 1.01}], []


def test_files_log_resume_no_data_and_one_retry(tmp_path):
    rows = [row(), row(session="2026-09-03"), row(session="2026-09-04")]
    api = Fake(throttle=1, empty={"2026-09-04"})
    counts = fq.fetch(api, rows, tmp_path, fq.Pacer(0.0), log=lambda *_: None, backoff=0, sleep=lambda s: None)
    assert counts == {"OK": 2, "NO_DATA": 1, "skipped": 0}
    p = fq.target(tmp_path, rows[0])
    assert p.exists() and gzip.decompress(p.read_bytes()).decode().startswith("time,bid_avg,ask_max,bid_min,ask_avg")
    log = [json.loads(x) for x in (tmp_path / "fetch_log.jsonl").read_text().splitlines()]
    assert [x["status"] for x in log] == ["OK", "OK", "NO_DATA"]
    api2 = Fake()
    assert fq.fetch(api2, rows, tmp_path, fq.Pacer(0.0), log=lambda *_: None)["skipped"] == 3 and api2.calls == []


def test_persistent_throttling_stops_the_run_for_a_later_resume(tmp_path):
    with pytest.raises(fq.probe.ProbeError, match="resume"):
        fq.fetch(Fake(throttle=5), [row()], tmp_path, fq.Pacer(0.0), log=lambda *_: None, backoff=0, sleep=lambda s: None)
    assert not fq.target(tmp_path, row()).exists()
