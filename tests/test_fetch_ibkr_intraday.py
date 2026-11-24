"""IBKR 1-minute fetcher: month files, manifests, resume, retries and the hold-out refusal (no TWS needed)."""

import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "fetch_ibkr_intraday", Path(__file__).resolve().parents[1] / "scripts" / "fetch_ibkr_intraday.py")
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)


def two_bar_schedule(month):
    return {s: 2 for s in fetch.month_schedule(month)}


class FakeSession:
    def __init__(self, failures=0, empty=(), week_misses=(), week_short=(), timeout_with_rows=0, dead_weeks=()):
        self.calls, self.failures, self.empty, self.week_misses = [], failures, set(empty), set(week_misses)
        self.week_short, self.timeout_with_rows, self.dead_weeks = set(week_short), timeout_with_rows, set(dead_weeks)

    def _rows(self, session):
        return [{"session": session, "minute": m, "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
                 "volume": 5.0, "wap": 10.0, "count": 2} for m in ("09:30", "09:31")]

    def _fail(self):
        if self.failures:
            self.failures -= 1
            return True
        return False

    def minute_bars(self, symbol, session, window):
        self.calls.append(session)
        if self._fail():
            return [], [("TIMEOUT", "no answer")]
        if session in self.empty:
            return [], [(162, "HMDS query returned no data")]
        return self._rows(session), []

    def week_bars(self, symbol, sessions):
        self.calls.append(tuple(sessions))
        if sessions[0] in self.dead_weeks:
            return {}, [("TIMEOUT", "no answer in 60s")]
        if self._fail():
            return {}, [("TIMEOUT", "no answer")]
        got = {s: self._rows(s)[:1 if s in self.week_short else 2] for s in sessions
               if s not in self.empty | self.week_misses}
        if self.timeout_with_rows:
            self.timeout_with_rows -= 1
            return got, [("TIMEOUT", "cut short")]
        return got, []


def test_a_month_is_fetched_a_week_per_request_written_with_a_manifest_and_skipped_on_rerun(tmp_path):
    api = FakeSession(empty={"2024-01-02"}, week_misses={"2023-12-13"})
    manifest = fetch.fetch_month(api, "AAA", "2023-12", "full", tmp_path, listed_from="2023-12-05", log=lambda *_: None,
                                 schedule=two_bar_schedule("2023-12"))
    assert manifest["sessions"] == 20 and manifest["by_status"] == {"BEFORE_FIRST_DAILY_BAR": 2, "OK": 18}
    assert api.calls == [("2023-12-05", "2023-12-06", "2023-12-07", "2023-12-08"),
                         ("2023-12-11", "2023-12-12", "2023-12-13", "2023-12-14", "2023-12-15"), "2023-12-13",
                         ("2023-12-18", "2023-12-19", "2023-12-20", "2023-12-21", "2023-12-22"),
                         ("2023-12-26", "2023-12-27", "2023-12-28", "2023-12-29")]
    assert manifest["bars"] == 36 and manifest["short_sessions"] == {}
    with gzip.open(tmp_path / "AAA" / "2023-12.csv.gz", "rt") as fh:
        lines = fh.read().splitlines()
    assert lines[0] == ",".join(fetch.COLUMNS) and lines[1].startswith("2023-12-05,09:30,10.0")
    assert not (tmp_path / "AAA" / "2023-12.partial.json").exists()
    again = FakeSession()
    assert fetch.fetch_month(again, "AAA", "2023-12", "full", tmp_path, listed_from=None) == manifest and not again.calls
    single = FakeSession(empty={"2024-01-03"})
    with pytest.raises(SystemExit, match="hold-out"):
        fetch.fetch_month(single, "AAA", "2024-01", "first-hour", tmp_path, listed_from=None)
    january = fetch.fetch_month(single, "AAA", "2024-01", "first-hour", tmp_path, listed_from=None,
                                allow_holdout=True, schedule=two_bar_schedule("2024-01"))
    assert all(isinstance(c, str) for c in single.calls) and january["empty_sessions"] == ["2024-01-03"]
    assert not list(tmp_path.glob("*/*.tmp"))


def test_a_session_the_week_returned_short_is_asked_for_alone_and_a_timeout_is_never_kept(tmp_path):
    api = FakeSession(week_short={"2023-12-13"}, timeout_with_rows=1)
    manifest = fetch.fetch_month(api, "AAA", "2023-12", "full", tmp_path, listed_from=None, log=lambda *_: None,
                                 backoff=0, schedule=two_bar_schedule("2023-12"))
    assert api.calls[0] == "2023-12-01" and api.calls[1] == api.calls[2]   # the week that timed out with rows, again
    assert "2023-12-13" in api.calls and manifest["bars"] == 2 * 20 and manifest["short_sessions"] == {}
    full = fetch.month_schedule("2023-11")
    assert full["2023-11-24"] == 210 and full["2023-11-22"] == 390
    assert fetch.month_schedule("2023-11", "first-hour")["2023-11-24"] == 60
    with gzip.open(tmp_path / "AAA" / "2023-12.csv.gz") as fh:
        fh.read()
    assert (tmp_path / "AAA" / "2023-12.csv.gz").read_bytes()[4:8] == b"\0\0\0\0"     # gzip mtime 0: rewrites hash alike


def test_throttling_retries_once_then_stops_with_progress_saved_and_resumes(tmp_path):
    api = FakeSession(failures=1)
    fetch.fetch_month(api, "AAA", "2023-11", "full", tmp_path, listed_from=None, log=lambda *_: None, backoff=0,
                      schedule=two_bar_schedule("2023-11"))
    assert api.calls[0] == api.calls[1]                        # the retry asked for the same week
    stuck = FakeSession(failures=100)               # the week, then each session alone, never answer
    with pytest.raises(fetch.probe.ProbeError):
        fetch.fetch_month(stuck, "BBB", "2023-11", "full", tmp_path, listed_from=None, log=lambda *_: None, backoff=0,
                          schedule=two_bar_schedule("2023-11"))
    progress = json.loads((tmp_path / "BBB" / "2023-11.partial.json").read_text())
    assert progress == {"sessions": {}, "window": "full"}
    resumed = FakeSession(failures=1)
    manifest = fetch.fetch_month(resumed, "BBB", "2023-11", "full", tmp_path, listed_from=None, log=lambda *_: None,
                                 backoff=0, schedule=two_bar_schedule("2023-11"))
    assert manifest["by_status"] == {"OK": 21}
    with pytest.raises(SystemExit, match="window"):
        (tmp_path / "CCC").mkdir()
        (tmp_path / "CCC" / "2023-11.partial.json").write_text(json.dumps({"sessions": {}, "window": "full"}))
        fetch.fetch_month(FakeSession(), "CCC", "2023-11", "first-hour", tmp_path, listed_from=None)


def test_holdout_months_and_remote_hosts_are_refused(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["fetch", "--months", "2023-12:2024-01"])
    with pytest.raises(SystemExit, match="hold-out"):
        fetch.main()
    monkeypatch.setattr(sys, "argv", ["fetch", "--months", "2023-12:2024-01", "--allow-holdout",
                                      "--out", "data/ibkr_intraday/v1-holdout"])
    with pytest.raises(SystemExit, match="alone"):
        fetch.main()
    monkeypatch.setattr(sys, "argv", ["fetch", "--months", "2024-01", "--allow-holdout"])
    with pytest.raises(SystemExit, match="own folder"):
        fetch.main()
    monkeypatch.setattr(sys, "argv", ["fetch", "--months", "2023-12", "--out", "/tmp/elsewhere"])
    with pytest.raises(SystemExit, match="data/"):
        fetch.main()
    monkeypatch.setattr(sys, "argv", ["fetch", "--months", "2023-12", "--host", "10.0.0.5"])
    with pytest.raises(SystemExit, match="non-local"):
        fetch.main()
    assert fetch.months_between("2023-11", "2024-02") == ["2023-11", "2023-12", "2024-01", "2024-02"]
    assert fetch.HOLDOUT_FROM == "2024-01"


def test_repair_asks_again_for_short_sessions_of_a_finished_month_and_keeps_the_longer_answer(tmp_path):
    schedule = two_bar_schedule("2023-12")
    fetch.fetch_month(FakeSession(empty={"2023-12-14"}), "AAA", "2023-12", "full", tmp_path, listed_from=None,
                      schedule=schedule)
    assert fetch.repair_month(FakeSession(), "AAA", "2023-11", "full", tmp_path) is None   # not fetched
    api = FakeSession()
    manifest = fetch.repair_month(api, "AAA", "2023-12", "full", tmp_path, schedule=schedule)
    assert api.calls == ["2023-12-14"] and manifest["repaired"] == ["2023-12-14"] and manifest["bars"] == 40
    with gzip.open(tmp_path / "AAA" / "2023-12.csv.gz", "rt") as fh:
        assert sum(line.startswith("2023-12-14") for line in fh) == 2
    again = FakeSession()
    assert fetch.repair_month(again, "AAA", "2023-12", "full", tmp_path, schedule=schedule)["repaired"] == ["2023-12-14"]
    assert again.calls == []
    with pytest.raises(SystemExit, match="hold-out"):
        fetch.repair_month(FakeSession(), "AAA", "2024-01", "full", tmp_path)


def test_a_week_ibkr_will_not_answer_whole_is_asked_for_day_by_day(tmp_path):
    api = FakeSession(dead_weeks={"2023-12-11"})
    manifest = fetch.fetch_month(api, "AAA", "2023-12", "full", tmp_path, listed_from=None, log=lambda *_: None,
                                 backoff=0, schedule=two_bar_schedule("2023-12"))
    dead = ("2023-12-11", "2023-12-12", "2023-12-13", "2023-12-14", "2023-12-15")
    assert api.calls.count(dead) == 2 and all(d in api.calls for d in dead)
    assert manifest["by_status"] == {"OK": 20} and manifest["bars"] == 40


def _at(text):
    from datetime import datetime, timezone
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _sessions_on_file(tmp_path, month="2023-12"):
    with gzip.open(tmp_path / "AAA" / f"{month}.csv.gz", "rt") as fh:
        return sorted({line.split(",")[0] for line in fh.read().splitlines()[1:]})


def test_a_session_that_has_not_closed_is_not_asked_for_and_the_month_stays_open(tmp_path):
    schedule = two_bar_schedule("2023-12")
    api = FakeSession()
    # Tuesday 2023-12-12, 21:10 UTC: the bell rang at 21:00, but the session counts as closed only SETTLE later.
    first = fetch.fetch_month(api, "AAA", "2023-12", "full", tmp_path, listed_from=None, schedule=schedule,
                              now=_at("2023-12-12T21:10:00"))
    assert api.calls == ["2023-12-01", ("2023-12-04", "2023-12-05", "2023-12-06", "2023-12-07", "2023-12-08"),
                         "2023-12-11"]
    assert first["open_sessions"][0] == "2023-12-12" and len(first["open_sessions"]) == 13
    assert first["sessions"] == 7 and first["by_status"] == {"OK": 7} and first["empty_sessions"] == []
    assert _sessions_on_file(tmp_path)[-1] == "2023-12-11"
    assert not (tmp_path / "AAA" / "2023-12.partial.json").exists()          # readers see a written month

    same_evening = FakeSession()
    assert fetch.fetch_month(same_evening, "AAA", "2023-12", "full", tmp_path, listed_from=None, schedule=schedule,
                             now=_at("2023-12-12T21:15:00")) == first and not same_evening.calls

    next_day = FakeSession()
    second = fetch.fetch_month(next_day, "AAA", "2023-12", "full", tmp_path, listed_from=None, schedule=schedule,
                               now=_at("2023-12-13T21:30:00"))
    assert next_day.calls == [("2023-12-12", "2023-12-13")]                   # only what closed since
    assert second["open_sessions"][0] == "2023-12-14" and second["sessions"] == 9
    assert _sessions_on_file(tmp_path)[-1] == "2023-12-13"

    end = FakeSession(empty={"2023-12-20"})
    done = fetch.fetch_month(end, "AAA", "2023-12", "full", tmp_path, listed_from=None, schedule=schedule,
                             now=_at("2024-01-02T12:00:00"))
    assert done["open_sessions"] == [] and done["sessions"] == 20 and done["empty_sessions"] == ["2023-12-20"]
    after = FakeSession()
    assert fetch.fetch_month(after, "AAA", "2023-12", "full", tmp_path, listed_from=None, schedule=schedule,
                             now=_at("2024-02-01T12:00:00")) == done and not after.calls   # a real empty answer is final


def test_a_month_closed_too_early_by_the_old_rule_is_reopened_and_keeps_what_it_has(tmp_path):
    schedule = two_bar_schedule("2023-12")
    # What the old rule left: asked on the 12th for the whole month, "no data" recorded for every later session.
    later = {s for s in schedule if s > "2023-12-12"}
    fetch.fetch_month(FakeSession(empty=later), "AAA", "2023-12", "full", tmp_path, listed_from="2023-12-04",
                      schedule=schedule, log=lambda *_: None, now=_at("2024-06-01T00:00:00"))
    path = tmp_path / "AAA" / "2023-12.json"
    old = json.loads(path.read_text())
    del old["open_sessions"]
    old["retrieved_at"] = "2023-12-13T00:54:39+00:00"
    path.write_text(json.dumps(old))
    before = (tmp_path / "AAA" / "2023-12.csv.gz").read_bytes()

    api = FakeSession()
    manifest = fetch.fetch_month(api, "AAA", "2023-12", "full", tmp_path, listed_from="2023-12-04", schedule=schedule,
                                 now=_at("2023-12-14T21:30:00"))
    assert api.calls == [("2023-12-13", "2023-12-14")]                        # nothing fetched before is asked again
    assert manifest["open_sessions"][0] == "2023-12-15" and manifest["empty_sessions"] == []
    assert manifest["by_status"] == {"BEFORE_FIRST_DAILY_BAR": 1, "OK": 9}
    after = gzip.decompress((tmp_path / "AAA" / "2023-12.csv.gz").read_bytes())
    assert after.startswith(gzip.decompress(before)) and len(after) > len(gzip.decompress(before))   # earlier rows unchanged
    assert _sessions_on_file(tmp_path)[-1] == "2023-12-14"

    # A month the old rule fetched after its last session closed is left alone.
    fetch.fetch_month(FakeSession(empty={"2023-11-20"}), "AAA", "2023-11", "full", tmp_path, listed_from=None,
                      schedule=two_bar_schedule("2023-11"), now=_at("2024-06-01T00:00:00"))
    path = tmp_path / "AAA" / "2023-11.json"
    old = json.loads(path.read_text())
    del old["open_sessions"]
    old["retrieved_at"] = "2023-12-02T00:00:00+00:00"
    path.write_text(json.dumps(old))
    quiet = FakeSession()
    assert fetch.fetch_month(quiet, "AAA", "2023-11", "full", tmp_path, listed_from=None,
                             schedule=two_bar_schedule("2023-11"), now=_at("2024-06-01T00:00:00")) == old
    assert not quiet.calls
    assert fetch.fetch_month(quiet, "AAA", "2023-11", "first-hour", tmp_path, listed_from=None,
                             now=_at("2024-06-01T00:00:00")) == old            # a finished month is never reopened


def test_a_shorter_second_answer_never_replaces_rows_on_file_and_repair_leaves_open_sessions_alone(tmp_path):
    schedule = two_bar_schedule("2023-12")
    fetch.fetch_month(FakeSession(), "AAA", "2023-12", "full", tmp_path, listed_from=None, schedule=schedule,
                      now=_at("2024-06-01T00:00:00"))
    path = tmp_path / "AAA" / "2023-12.json"
    old = json.loads(path.read_text())
    del old["open_sessions"]
    old["retrieved_at"] = "2023-12-28T00:00:00+00:00"                         # before the 28th and 29th closed
    path.write_text(json.dumps(old))
    manifest = fetch.fetch_month(FakeSession(empty={"2023-12-28", "2023-12-29"}), "AAA", "2023-12", "full", tmp_path,
                                 listed_from=None, schedule=schedule, log=lambda *_: None, now=_at("2024-06-01T00:00:00"))
    assert manifest["bars"] == 40 and manifest["empty_sessions"] == [] and manifest["open_sessions"] == []

    fetch.fetch_month(FakeSession(week_short={"2023-11-01"}, empty=set()), "AAA", "2023-11", "full", tmp_path,
                      listed_from=None, schedule=two_bar_schedule("2023-11"), now=_at("2023-11-02T12:00:00"))
    api = FakeSession()
    repaired = fetch.repair_month(api, "AAA", "2023-11", "full", tmp_path, schedule=two_bar_schedule("2023-11"),
                                  now=_at("2023-11-02T12:00:00"))
    assert api.calls == [] and repaired["open_sessions"][0] == "2023-11-02"   # the 1st is whole, the rest are not closed


def _legacy(tmp_path, retrieved_at, month="2023-12", **extra):
    """Rewrite a month's manifest as the old rule left it: no list of open sessions."""
    path = tmp_path / "AAA" / f"{month}.json"
    old = json.loads(path.read_text())
    del old["open_sessions"]
    old.update(retrieved_at=retrieved_at, **extra)
    path.write_text(json.dumps(old))
    return old


def test_an_interrupted_reopen_leaves_the_written_month_as_it_was(tmp_path):
    schedule = two_bar_schedule("2023-12")
    fetch.fetch_month(FakeSession(), "AAA", "2023-12", "full", tmp_path, listed_from=None, schedule=schedule,
                      now=_at("2024-06-01T00:00:00"))
    old = _legacy(tmp_path, "2023-12-28T00:00:00+00:00")                      # before the 28th and 29th closed
    data = tmp_path / "AAA" / "2023-12.csv.gz"
    before = data.read_bytes()
    with pytest.raises(fetch.probe.ProbeError):                               # the week, then the day, unanswered twice each
        fetch.fetch_month(FakeSession(failures=4), "AAA", "2023-12", "full", tmp_path, listed_from=None,
                          schedule=schedule, log=lambda *_: None, backoff=0, now=_at("2024-06-01T00:00:00"))
    assert data.read_bytes() == before and json.loads((tmp_path / "AAA" / "2023-12.json").read_text()) == old
    assert not (tmp_path / "AAA" / "2023-12.partial.json").exists()           # readers still see a written month
    manifest = fetch.fetch_month(FakeSession(empty={"2023-12-28", "2023-12-29"}), "AAA", "2023-12", "full", tmp_path,
                                 listed_from=None, schedule=schedule, log=lambda *_: None, now=_at("2024-06-01T00:00:00"))
    assert manifest["bars"] == 40 and manifest["empty_sessions"] == []        # the rows on file outlast "no data"


def test_progress_left_beside_a_written_month_is_not_trusted_and_rows_before_the_first_daily_bar_stay(tmp_path):
    schedule = two_bar_schedule("2023-12")
    fetch.fetch_month(FakeSession(), "AAA", "2023-12", "full", tmp_path, listed_from=None, schedule=schedule,
                      now=_at("2023-12-12T21:30:00"))
    _legacy(tmp_path, "2023-12-13T00:00:00+00:00")
    stale = {"window": "full", "sessions": {s: {"status": "NO_BARS", "rows": []} for s in schedule}}
    (tmp_path / "AAA" / "2023-12.partial.json").write_text(json.dumps(stale))
    api = FakeSession()
    manifest = fetch.fetch_month(api, "AAA", "2023-12", "full", tmp_path, listed_from="2023-12-08", schedule=schedule,
                                 now=_at("2023-12-14T21:30:00"))
    assert api.calls == [("2023-12-13", "2023-12-14")] and manifest["empty_sessions"] == []
    assert manifest["by_status"] == {"OK": 10} and manifest["bars"] == 20     # rows before the 8th are on file: kept
    assert not (tmp_path / "AAA" / "2023-12.partial.json").exists()


def test_a_month_checked_by_a_repair_is_reopened_from_that_check_and_keeps_the_repair_note(tmp_path):
    schedule = two_bar_schedule("2023-12")
    fetch.fetch_month(FakeSession(), "AAA", "2023-12", "full", tmp_path, listed_from=None, schedule=schedule,
                      now=_at("2023-12-14T21:30:00"))
    _legacy(tmp_path, "2023-12-12T00:54:00+00:00", checked_complete_at="2023-12-15T03:30:00+00:00",
            repaired=["2023-12-13", "2023-12-14"])
    api = FakeSession()
    manifest = fetch.fetch_month(api, "AAA", "2023-12", "full", tmp_path, listed_from=None, schedule=schedule,
                                 now=_at("2023-12-15T21:30:00"))
    assert api.calls == ["2023-12-15"] and manifest["repaired"] == ["2023-12-13", "2023-12-14"]
    assert manifest["open_sessions"][0] == "2023-12-18" and manifest["bars"] == 22


def test_an_early_close_counts_as_closed_twenty_minutes_after_its_own_bell():
    closes = fetch.session_closes(["2023-11-22", "2023-11-24"])
    assert closes["2023-11-22"] == _at("2023-11-22T21:20:00") and closes["2023-11-24"] == _at("2023-11-24T18:20:00")
