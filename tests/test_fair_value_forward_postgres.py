"""Forward tracker against real Postgres: freeze, replay, score, miss, timing.

Nothing here depends on today's date: every run passes an explicit clock, and
the test schema's recorded_at default follows a per-connection setting that
the fixture sets to the simulated database time.
"""

import math
from datetime import datetime, timezone

import pytest

from stock_machine import db, research_store
from stock_machine.agent_intelligence import fair_value_forward as f
from stock_machine.market_calendar import session_dates
from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)

DAYS = session_dates("2025-06-02", "2027-12-31")
DB_NOW = {"t": None}


def at(*args):
    return datetime(*args, tzinfo=timezone.utc)


def run(now, clock_now=None, db_now=None):
    """One scheduled run: the job's clock, and the database's own recording time."""
    DB_NOW["t"] = db_now or clock_now or now
    return f.run_daily(now=now, clock=lambda: clock_now or now)


def seed(conn, ticker, drift, start=100.0, days=DAYS):
    rows, price = [], start
    for i, day in enumerate(days):
        price *= 1 + drift + 0.006 * math.sin(i / 4)
        rows.append((ticker, day, price, price * 1.01, price * 0.99, price, price, 1_000_000))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO prices_daily VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", rows)


@pytest.fixture
def tracker(pg, monkeypatch):  # noqa: F811
    monkeypatch.setattr(f, "PROTOCOL", {**f.PROTOCOL, "universe": ["VZ"]})
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    with pg() as conn:
        conn.execute("""ALTER TABLE research_evidence_records ALTER COLUMN recorded_at SET DEFAULT
            COALESCE(NULLIF(current_setting('sm.test_now', true), '')::timestamptz, clock_timestamp())""")
        seed(conn, "VZ", 0.0008)
        seed(conn, "SPY", 0.0004, 400.0)
        conn.commit()

    def connect():
        c = pg()
        if DB_NOW["t"] is not None:
            c.execute("SELECT set_config('sm.test_now', %s, false)", (DB_NOW["t"].isoformat(),))
            c.commit()
        return c
    monkeypatch.setattr(db, "connect", connect)
    DB_NOW["t"] = None
    yield connect
    DB_NOW["t"] = None


def count(pg, kind, where="", params=()):  # noqa: F811
    with pg() as conn:
        return conn.execute(f"SELECT count(*) FROM research_evidence_records WHERE kind=%s {where}",
                            (kind, *params)).fetchone()[0]


def delete_bar(pg, ticker, day):  # noqa: F811
    with pg() as conn:
        conn.execute("DELETE FROM prices_daily WHERE ticker=%s AND date=%s", (ticker, day))
        conn.commit()


NIGHT = at(2026, 10, 31, 4, 20)   # after the 2026-10-30 close; deadline 2026-11-02 14:30 UTC


def test_freeze_replay_and_score_one_month(tracker):
    with tracker() as conn:
        before = f.summary(conn, now=NIGHT)
    assert before["freeze_window_open"] and not before["freeze_missing"]
    first = run(NIGHT)
    assert first["status"] == "OK" and first["freeze"]["status"] == "FROZEN"
    assert first["freeze"]["counts"] == {"LONG": 0, "SHORT": 0, "FLAT": 1, "NO_DECISION": 0}  # no filings: never solid
    assert run(at(2026, 11, 2, 11, 50))["freeze"]["status"] == "REPLAYED"
    assert count(tracker, f.DECISION_KIND) == 1 and count(tracker, f.PROTOCOL_KIND) == 1
    with tracker() as conn:
        status = f.summary(conn, now=at(2026, 11, 3, 12))
    assert status["protocol_registered"] and not status["freeze_missing"] and not status["freeze_window_open"]
    assert status["next_due_session"] == "2027-04-27" and status["matured_due_unscored"] == []
    assert status["evaluation"]["status"] == "PENDING_EVIDENCE" and status["decision_code_versions"] == 1
    assert run(at(2027, 4, 27, 12))["outcomes"]["scored"] == []          # not yet due
    matured = run(at(2027, 4, 28, 4, 20))
    assert matured["outcomes"]["scored"] == ["2026-10-30"]
    with tracker() as conn:
        out = research_store.get(conn, f.OUTCOME_KIND, f"{f.FORWARD_ID}:2026-10-30")["payload"]
    assert out["status"] == "SCORED" and out["monthly_mean"] == 0.0 and out["active"] is False
    assert (out["entry_session"], out["due_session"]) == ("2026-11-02", "2027-04-27")
    assert run(at(2027, 4, 29, 4, 20))["outcomes"]["scored"] == []
    assert count(tracker, f.OUTCOME_KIND) == 1


def test_a_missed_month_is_recorded_once_and_never_reconstructed(tracker):
    missed = run(at(2026, 11, 3, 4, 20))      # after the 2026-11-02 open
    assert missed["status"] == "ATTENTION" and missed["freeze"]["status"] == "FREEZE_MISSED"
    later = run(at(2026, 11, 4, 4, 20))
    assert later["freeze"]["status"] == "MISSED_ALREADY_RECORDED"
    assert later["status"] == "ATTENTION" and later["sticky_attention"] == ["FREEZE_MISSED:2026-10-30"]
    assert run(at(2026, 11, 20, 4, 20))["status"] == "OK"                 # sticky for a week, then quiet
    assert count(tracker, f.DECISION_KIND) == 0 and count(tracker, f.CHECK_KIND) == 1
    with tracker() as conn:
        status = f.summary(conn, now=at(2026, 11, 20, 12))
    assert status["freeze_missing"] and status["missed_months"] == ["2026-10-30"]


def test_months_missed_during_a_long_outage_are_all_recorded(tracker):
    assert run(NIGHT)["freeze"]["status"] == "FROZEN"
    back = run(at(2027, 1, 2, 4, 20))          # first run after the 2026-12-31 close
    assert back["freeze"]["status"] == "FROZEN" and back["freeze"]["decision_session"] == "2026-12-31"
    assert back["freeze"]["newly_missed"] == ["2026-11-30"] and back["status"] == "ATTENTION"
    with tracker() as conn:
        assert f.summary(conn, now=at(2027, 1, 4, 12))["missed_months"] == ["2026-11-30"]


def test_missing_bars_wait_for_data_until_the_final_attempt(tracker):
    delete_bar(tracker, "VZ", "2026-10-30")
    night = run(NIGHT)
    assert night["status"] == "ATTENTION" and night["freeze"]["status"] == "FREEZE_PENDING_DATA"
    assert night["freeze"]["missing"] == ["VZ"] and not night["freeze"]["final_attempt"]
    assert count(tracker, f.DECISION_KIND) == 0
    for minute in (10 * 60 + 45, 11 * 60 + 45, 12 * 60 + 30):           # every morning cron is final in EST
        assert 0 < (f.session_open("2026-11-02") - at(2026, 11, 2, minute // 60, minute % 60)).total_seconds() \
            <= f.FINAL_ATTEMPT_HOURS * 3600
    final = run(at(2026, 11, 2, 10, 50))
    assert final["freeze"]["status"] == "FROZEN" and final["freeze"]["missing_bars_at_freeze"] == ["VZ"]
    assert final["freeze"]["counts"]["NO_DECISION"] == 1


def test_without_a_spy_bar_the_month_is_never_frozen(tracker):
    delete_bar(tracker, "SPY", "2026-10-30")
    final = run(at(2026, 11, 2, 11, 50))
    assert final["freeze"]["status"] == "FREEZE_PENDING_DATA" and final["freeze"]["final_attempt"]
    assert run(at(2026, 11, 2, 15))["freeze"]["status"] == "FREEZE_MISSED"
    assert count(tracker, f.DECISION_KIND) == 0


def test_a_freeze_that_would_land_after_the_open_is_not_written(tracker):
    late = run(at(2026, 11, 2, 12, 30), clock_now=at(2026, 11, 2, 14, 31))   # the load ran past the open
    assert late["freeze"]["status"] == "FREEZE_MISSED" and count(tracker, f.DECISION_KIND) == 0


def test_a_scoring_failure_never_blocks_the_freeze(tracker, monkeypatch):
    assert run(NIGHT)["freeze"]["status"] == "FROZEN"

    def broken(*args, **kwargs):
        raise RuntimeError("vendor outage")
    monkeypatch.setattr(f, "score_month", broken)
    next_month_end = run(at(2027, 5, 1, 4, 20))    # 2026-10 is due; 2027-04-30 must still freeze
    assert next_month_end["freeze"]["status"] == "FROZEN"
    assert next_month_end["freeze"]["decision_session"] == "2027-04-30"
    errors = next_month_end["outcomes"]["errors"]
    assert errors == [{"decision_session": "2026-10-30", "error": "RuntimeError: vendor outage"}]
    assert next_month_end["status"] == "ATTENTION"
    assert next_month_end["evaluation"]["status"] == "PENDING_EVIDENCE"


def test_a_blocked_month_is_checked_once_per_session(tracker):
    assert run(NIGHT)["freeze"]["status"] == "FROZEN"
    delete_bar(tracker, "VZ", "2027-02-01")
    first = run(at(2027, 5, 1, 4, 20))
    assert first["outcomes"]["blocked"] == ["2026-10-30"]
    with tracker() as conn:   # a repair between runs of the same completed session changes the data
        seed(conn, "VZ", 0.0008, days=["2027-02-01"])
        conn.commit()
    again = run(at(2027, 5, 3, 10, 45))
    assert again["status"] != "FAILED" and again["outcomes"]["blocked"] == [] and again["outcomes"]["scored"] == []
    assert count(tracker, f.CHECK_KIND, "AND payload->>'status'='OUTCOME_BLOCKED_MISSING_BARS'") == 1
    assert run(at(2027, 5, 4, 4, 20))["outcomes"]["scored"] == ["2026-10-30"]     # next session: scored


def test_a_decision_recorded_after_its_deadline_is_never_scored(tracker):
    run(NIGHT)  # registers the protocol
    DB_NOW["t"] = at(2026, 12, 1, 15)               # the database records it after the 12-01 open
    with tracker() as conn:
        protocol = research_store.get(conn, f.PROTOCOL_KIND, f.FORWARD_ID)
        payload = f.decision_payload("2026-11-30", [], protocol=protocol, created_at=at(2026, 11, 30, 23),
                                     missing=[])
        research_store.save(conn, f.DECISION_KIND, f"{f.FORWARD_ID}:2026-11-30", payload)
        conn.commit()
    due = datetime.fromisoformat(payload["due_session"] + "T23:00:00+00:00")
    result = run(due)                                # scored right after it matures
    assert "2026-11-30" in result["outcomes"]["invalid"] and result["status"] == "ATTENTION"
    with tracker() as conn:
        assert research_store.get(conn, f.OUTCOME_KIND, f"{f.FORWARD_ID}:2026-11-30")["payload"]["status"] == (
            "INVALID_PROSPECTIVE_TIMING")
        status = f.summary(conn, now=due)
    assert "INVALID_PROSPECTIVE_TIMING:2026-11-30" in status["sticky_attention"]
    assert status["invalid_months"] == ["2026-11-30"]


def test_a_freeze_recorded_after_the_open_is_flagged_at_once(tracker):
    late = run(at(2026, 11, 2, 12, 30), db_now=at(2026, 11, 2, 14, 31))   # insert blocked past the open
    assert late["freeze"]["status"] == "FREEZE_LATE" and late["status"] == "ATTENTION"
    assert count(tracker, f.DECISION_KIND) == 1
    assert count(tracker, f.CHECK_KIND, "AND payload->>'status'='FREEZE_LATE'") == 1
    again = run(at(2026, 11, 3, 4, 20))
    assert again["freeze"]["status"] == "REPLAYED" and again["status"] == "ATTENTION"   # sticky for a week
    with tracker() as conn:
        assert f.summary(conn, now=at(2026, 11, 3, 12))["missed_months"] == ["2026-10-30"]


def test_a_lost_late_marker_is_repaired_on_the_next_run(tracker):
    DB_NOW["t"] = at(2026, 11, 2, 14, 31)
    run(at(2026, 11, 2, 12, 30), db_now=at(2026, 11, 2, 14, 31))
    with tracker() as conn:   # simulate the marker never having been written (crash)
        conn.execute("ALTER TABLE research_evidence_records DISABLE TRIGGER USER")
        conn.execute("DELETE FROM research_evidence_records WHERE kind=%s", (f.CHECK_KIND,))
        conn.execute("ALTER TABLE research_evidence_records ENABLE TRIGGER USER")
        conn.commit()
    repaired = run(at(2026, 11, 3, 4, 20))
    assert repaired["freeze"]["status"] == "FREEZE_LATE"
    assert count(tracker, f.CHECK_KIND, "AND payload->>'status'='FREEZE_LATE'") == 1


def scored_outcome(i, value):
    session = f.month_end_session(f"{2027 + i // 12}-{i % 12 + 1:02d}-01")
    return session, {"status": "SCORED", "forward_id": f.FORWARD_ID, "decision_session": session,
                     "monthly_mean": value, "active": True}


def test_the_evaluation_is_written_once_and_stays_the_reported_result(tracker):
    with tracker() as conn:
        for i in range(24):
            session, payload = scored_outcome(i, 0.05 + 0.01 * (i % 3))
            research_store.save(conn, f.OUTCOME_KIND, f"{f.FORWARD_ID}:{session}", payload)
        conn.commit()
    first = run(at(2029, 1, 3, 4, 20))
    assert first["evaluation"]["status"] == "PASS_REQUIRES_INDEPENDENT_REVIEW" and first["evaluation"]["final"]
    with tracker() as conn:
        for i in range(24, 30):
            session, payload = scored_outcome(i, -2.0)
            research_store.save(conn, f.OUTCOME_KIND, f"{f.FORWARD_ID}:{session}", payload)
        conn.commit()
    later = run(at(2029, 7, 3, 4, 20))
    assert later["evaluation"]["status"] == "PASS_REQUIRES_INDEPENDENT_REVIEW" and later["evaluation"]["final"]
    assert count(tracker, f.EVALUATION_KIND) == 1
    with tracker() as conn:
        shown = f.summary(conn, now=at(2029, 7, 3, 12))["evaluation"]
    assert shown["final"] and shown["months"] == 24
    assert shown["post_final_monitoring"]["scored_months_available"] == 30


def test_a_changed_protocol_or_decision_behavior_refuses_to_run(tracker, monkeypatch):
    run(NIGHT)
    monkeypatch.setattr(f, "PROTOCOL", {**f.PROTOCOL, "universe": ["VZ", "AAPL"]})
    assert run(NIGHT)["reason_code"] == "FORWARD_PROTOCOL_CHANGED"
    monkeypatch.setattr(f, "PROTOCOL", {**f.PROTOCOL, "universe": ["VZ"]})
    monkeypatch.setattr(f, "DECISION_BEHAVIOR_SHA256", "0" * 64)
    assert run(NIGHT)["reason_code"] == "DECISION_BEHAVIOR_CHANGED"
    monkeypatch.setattr(f, "REPLAY_PROTOCOL_SHA256", "0" * 64)
    assert run(NIGHT)["reason_code"] == "REPLAY_PROTOCOL_CHANGED"


def test_only_main_writes_evidence(tracker, monkeypatch):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/feature")
    assert run(NIGHT)["status"] == "REFUSED_NON_MAIN"
    assert count(tracker, f.PROTOCOL_KIND) == 0


def test_no_final_verdict_while_an_earlier_month_is_unresolved(tracker):
    run(NIGHT)   # registers; freezes 2026-10-30, which is not yet scored
    with tracker() as conn:
        for i in range(24):
            session, payload = scored_outcome(i + 1, 0.05 + 0.01 * (i % 3))   # 2027-02 onward
            research_store.save(conn, f.OUTCOME_KIND, f"{f.FORWARD_ID}:{session}", payload)
        conn.commit()
    pending = run(at(2027, 4, 1, 4, 20))   # 2026-10-30 is not due until 2027-04-27
    assert pending["evaluation"]["status"] == "EVALUATION_DUE" and pending["evaluation"]["final"] is False
    assert count(tracker, f.EVALUATION_KIND) == 0


def test_a_scoring_code_change_never_blocks_the_freeze(tracker, monkeypatch):
    monkeypatch.setattr(f, "SCORING_BEHAVIOR_SHA256", "0" * 64)
    result = run(NIGHT)
    assert result["freeze"]["status"] == "FROZEN" and result["status"] == "ATTENTION"
    assert result["outcomes"]["errors"] == [{"error": "ProtocolChanged: SCORING_BEHAVIOR_CHANGED"}]


def test_a_decision_code_change_is_flagged_and_stays_red_until_reviewed(tracker, monkeypatch):
    assert run(NIGHT)["freeze"]["status"] == "FROZEN"
    monkeypatch.setattr(f, "source_fingerprint", lambda: "f" * 64)
    flagged = run(at(2026, 12, 1, 4, 20))                         # freezes 2026-11-30 with changed code
    assert flagged["freeze"]["status"] == "FROZEN" and flagged["freeze"]["decision_sources_changed"]
    assert flagged["status"] == "ATTENTION"
    before_open = run(at(2026, 12, 1, 10, 45))                    # replayed, but the change stays visible
    assert before_open["freeze"]["status"] == "REPLAYED" and before_open["status"] == "ATTENTION"
    assert "DECISION_SOURCES_CHANGED:2026-11-30" in before_open["sticky_attention"]


def test_a_failed_audit_turns_the_run_red(tracker, monkeypatch):
    from stock_machine.admin_panel import store
    real = store.audit

    def flaky(conn, actor, event, details):
        if event.endswith("FINISHED"):
            raise RuntimeError("lock connection dropped")
        return real(conn, actor, event, details)
    monkeypatch.setattr(store, "audit", flaky)
    result = run(NIGHT)
    assert result["freeze"]["status"] == "FROZEN" and result["audit_failed"] and result["status"] == "ATTENTION"


def test_a_lock_connection_failure_still_returns_a_receipt(tracker, monkeypatch):
    def down():
        raise RuntimeError("database unreachable")
    monkeypatch.setattr(db, "connect", down)
    result = run(NIGHT)
    assert result["status"] == "FAILED" and "database unreachable" in result["reason_code"]


def test_a_code_change_is_recorded_once_against_the_previous_month(tracker, monkeypatch):
    assert run(NIGHT)["freeze"]["status"] == "FROZEN"
    monkeypatch.setattr(f, "source_fingerprint", lambda: "f" * 64)
    assert run(at(2026, 12, 1, 4, 20))["freeze"]["decision_sources_changed"]
    later = run(at(2027, 1, 2, 4, 20))                             # same changed code, next month-end
    assert later["freeze"]["status"] == "FROZEN" and not later["freeze"]["decision_sources_changed"]
    assert later["status"] == "OK" and later["sticky_attention"] == []
    assert count(tracker, f.CHECK_KIND, "AND payload->>'status'='DECISION_SOURCES_CHANGED'") == 1


def test_a_partial_freeze_stays_visible_for_a_week(tracker):
    delete_bar(tracker, "VZ", "2026-10-30")
    final = run(at(2026, 11, 2, 10, 50))
    assert final["freeze"]["status"] == "FROZEN" and final["status"] == "ATTENTION"
    replayed = run(at(2026, 11, 2, 12, 30))                       # the later morning runs stay red
    assert replayed["freeze"]["status"] == "REPLAYED" and replayed["status"] == "ATTENTION"
    assert replayed["sticky_attention"] == ["FROZEN_WITH_MISSING_BARS:2026-10-30"]
    assert run(at(2026, 11, 20, 4, 20))["status"] == "OK"


def test_an_excluded_outcome_is_recorded_and_reported(tracker):
    assert run(NIGHT)["freeze"]["status"] == "FROZEN"
    delete_bar(tracker, "VZ", "2027-02-01")
    expired = run(at(2027, 5, 12, 4, 20))                         # 10 sessions after the 2027-04-27 due date
    assert expired["outcomes"]["scored"] == ["2026-10-30"] and expired["outcomes"]["excluded"] == ["2026-10-30"]
    assert expired["status"] == "ATTENTION" and "OUTCOME_EXCLUDED_MISSING_BARS:2026-10-30" in expired["sticky_attention"]
    with tracker() as conn:
        out = research_store.get(conn, f.OUTCOME_KIND, f"{f.FORWARD_ID}:2026-10-30")["payload"]
        check = research_store.get(conn, f.CHECK_KIND, f"{f.FORWARD_ID}:2026-10-30:OUTCOME_EXCLUDED_MISSING_BARS")
    assert out["excluded"] == 1 and out["monthly_mean"] is None and out["code"]["decision_sources_sha256"]
    assert check["payload"]["excluded"] == ["VZ"] and check["payload"]["month_without_mean"] is True


def test_a_moved_tracked_file_is_reported_nightly_and_never_costs_a_month(tracker, monkeypatch):
    assert run(NIGHT)["freeze"]["status"] == "FROZEN"
    monkeypatch.setattr(f, "DECISION_SOURCES", (*f.DECISION_SOURCES, "backtest/moved_away.py"))
    quiet_day = run(at(2026, 11, 17, 4, 20))
    assert quiet_day["status"] == "ATTENTION" and quiet_day["missing_sources"] == ["backtest/moved_away.py"]
    month_end = run(at(2026, 12, 1, 4, 20))
    assert month_end["freeze"]["status"] == "FROZEN" and month_end["freeze"]["decision_sources_changed"]
