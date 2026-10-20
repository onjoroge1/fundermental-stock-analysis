"""Strategy forward shadow against real Postgres: freeze, miss, timing, trades, isolation.

Every run passes an explicit clock; the test schema's recorded_at default
follows a per-connection setting set to the simulated database time.
"""

from datetime import datetime, timezone

import pytest

from stock_machine import db, research_store
from stock_machine.agent_intelligence import fair_value_forward as fvf
from stock_machine.agent_intelligence import strategy_forward as f
from stock_machine.backtest import event_replay as er
from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)
from tests.test_strategy_forward import BREAKOUT, SPY

DB_NOW = {"t": None}
FIRST_NIGHT = datetime(2026, 10, 27, 4, 20, tzinfo=timezone.utc)      # after the 10-26 close
BREAKOUT_NIGHT = datetime(2026, 10, 28, 4, 20, tzinfo=timezone.utc)   # after the 10-27 (signal) close
WINDOW_DONE = datetime(2026, 11, 26, 4, 20, tzinfo=timezone.utc)      # after the 11-25 close (S1 window end)


def at(*args):
    return datetime(*args, tzinfo=timezone.utc)


def no_filings(ciks, session, **kwargs):
    return {t: [] for t in ciks}, []


def run(now, clock_now=None, db_now=None, fetch=no_filings):
    DB_NOW["t"] = db_now or clock_now or now
    return f.run_daily(now=now, clock=lambda: clock_now or now, fetch_events=fetch)


@pytest.fixture
def shadow(pg, monkeypatch):  # noqa: F811
    monkeypatch.setattr(f, "PROTOCOL", {**f.PROTOCOL, "universe": ["VZ"]})
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    with pg() as conn:
        conn.execute("""ALTER TABLE research_evidence_records ALTER COLUMN recorded_at SET DEFAULT
            COALESCE(NULLIF(current_setting('sm.test_now', true), '')::timestamptz, clock_timestamp())""")
        with conn.cursor() as cur:
            for ticker, rows in (("VZ", BREAKOUT), ("SPY", SPY)):
                cur.executemany("INSERT INTO prices_daily VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                                [(ticker, r["date"], r["open"], r["high"], r["low"], r["close"], r["adj_close"],
                                  r["volume"]) for r in rows])
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
        return conn.execute(f"SELECT count(*) FROM research_evidence_records WHERE kind=%s "
                            f"AND payload->>'forward_id'=%s {where}", (kind, f.FORWARD_ID, *params)).fetchone()[0]


def sql(pg, statement, params=()):  # noqa: F811
    with pg() as conn:
        conn.execute(statement, params)
        conn.commit()


def frozen(pg, session):  # noqa: F811
    with pg() as conn:
        return research_store.get(conn, f.DECISION_KIND, f"{f.FORWARD_ID}:{session}")["payload"]


def test_freeze_replay_and_one_trade_end_to_end(shadow):
    first = run(FIRST_NIGHT)
    assert first["status"] == "OK" and first["freeze"]["status"] == "FROZEN"
    assert first["freeze"]["counts"] == {s: 0 for s in f.STRATEGIES}
    breakout = run(BREAKOUT_NIGHT)
    assert breakout["freeze"]["counts"]["S1_volume_breakout"] == 1 and breakout["status"] == "OK"
    assert breakout["freeze"]["counts"]["S4_value_momentum"] == 0      # no filings: never a solid company
    assert breakout["freeze"]["s4_source"] == "REUSED:2026-10-26"      # observed once per month-end
    assert run(at(2026, 10, 28, 12, 35))["freeze"]["status"] == "REPLAYED"
    assert count(shadow, f.DECISION_KIND) == 2
    with shadow() as conn:
        view = f.summary(conn, now=at(2026, 10, 28, 12, 45), ticker="VZ")
    assert view["trades_in_progress"] == [{"ticker": "VZ", "strategy": "S1_volume_breakout", "signal": "2026-10-27",
                                           "entry": "2026-10-28", "window_ends": "2026-11-25",
                                           "status": "AWAITING_ENTRY"}]
    assert view["unresolved_trades"]["S1_volume_breakout"] == 1
    assert view["latest_decision"]["rows"][0]["flags"]["S1_volume_breakout"] is True
    assert view["use"] == "INFORMATION_ONLY" and view["evaluation"] == {"status": "PENDING", "final": False}
    assert "verdict" not in view["monitoring"]["strategies"]["S1_volume_breakout"]
    matured = run(WINDOW_DONE)
    assert matured["outcomes"]["scored"] == 1 and matured["status"] == "ATTENTION"   # sessions in between missed
    with shadow() as conn:
        trade = research_store.get(conn, f.OUTCOME_KIND, f.outcome_key("S1_volume_breakout", "VZ", "2026-10-27"))
        assert trade["payload"]["status"] == "SCORED" and trade["payload"]["exit"] == "2026-11-25"
        # The fair-value tracker shares the evidence kinds but never sees these records.
        assert fvf.decisions(conn) == [] and fvf.outcomes(conn) == []
        assert fvf.summary(conn, now=at(2026, 11, 26, 12))["frozen_months"] == []
    assert run(at(2026, 11, 28, 4, 20))["outcomes"]["scored"] == 0


def test_missed_sessions_are_recorded_once_and_never_reconstructed(shadow):
    late = run(at(2026, 10, 27, 14, 0))                        # after the 10-27 open
    assert late["freeze"]["status"] == "FREEZE_MISSED" and late["status"] == "ATTENTION"
    back = run(at(2026, 10, 30, 4, 20))                        # after the 10-29 close
    assert back["freeze"]["status"] == "FROZEN" and back["freeze"]["decision_session"] == "2026-10-29"
    assert back["freeze"]["newly_missed"] == ["2026-10-27", "2026-10-28"]
    assert "FREEZE_MISSED:2026-10-26" in back["sticky_attention"]
    with shadow() as conn:
        status = f.summary(conn, now=at(2026, 10, 30, 12))
    assert status["missed_sessions"] == ["2026-10-26", "2026-10-27", "2026-10-28"] and status["frozen_sessions"] == 1


def test_missing_bars_wait_until_a_final_attempt(shadow):
    sql(shadow, "DELETE FROM prices_daily WHERE ticker='VZ' AND date='2026-10-26'")
    assert run(FIRST_NIGHT)["freeze"] == {"status": "FREEZE_PENDING_DATA", "decision_session": "2026-10-26",
                                          "missing": ["VZ"], "final_attempt": False, "newly_missed": []}
    assert run(at(2026, 10, 27, 10, 50))["freeze"]["status"] == "FREEZE_PENDING_DATA"   # 2h40 before: waits
    final = run(at(2026, 10, 27, 11, 50))                      # within 2h of the 13:30 UTC open
    assert final["freeze"]["status"] == "FROZEN" and final["freeze"]["missing_bars_at_freeze"] == ["VZ"]
    assert final["status"] == "ATTENTION"                       # a stock without a decision is never green
    assert frozen(shadow, "2026-10-26")["with_bar"] == []


def test_an_incomplete_spy_bar_counts_as_missing(shadow):
    sql(shadow, "UPDATE prices_daily SET high=close*0.9 WHERE ticker='SPY' AND date='2026-10-26'")
    final = run(at(2026, 10, 27, 12, 35))
    assert final["freeze"]["status"] == "FREEZE_PENDING_DATA" and final["freeze"]["missing"] == ["SPY"]
    assert run(at(2026, 10, 27, 14, 0))["freeze"]["status"] == "FREEZE_MISSED"
    assert count(shadow, f.DECISION_KIND) == 0


def test_unread_filings_wait_then_leave_only_s3_undecided_within_the_time_budget(shadow):
    budgets = []

    def sec_down(ciks, session, budget_seconds):
        budgets.append(budget_seconds)
        return {}, sorted(ciks)
    assert run(FIRST_NIGHT)["freeze"]["status"] == "FROZEN"
    night = run(BREAKOUT_NIGHT, fetch=sec_down)
    assert night["freeze"]["status"] == "FREEZE_PENDING_DATA" and night["freeze"]["sec_unavailable"] == ["VZ"]
    final = run(at(2026, 10, 28, 13, 15), fetch=sec_down)       # 15 minutes before the open
    assert final["freeze"]["status"] == "FROZEN" and final["freeze"]["sec_unavailable"] == ["VZ"]
    assert budgets == [f.SEC_BUDGET_SECONDS, 300]               # never past the open minus the margin
    row, = frozen(shadow, "2026-10-27")["rows"]
    assert row["flags"]["S1_volume_breakout"] is True and row["flags"]["S3_post_earnings"] is None


def test_s4_is_observed_once_per_month_end_and_its_failure_never_costs_the_session(shadow, monkeypatch):
    calls = []

    def eligible(conn, tickers, month_ends):
        calls.append(month_ends)
        return {month_ends[0]: {"VZ"}}
    monkeypatch.setattr(er, "_s4_eligibility", eligible)
    run(FIRST_NIGHT)
    assert run(BREAKOUT_NIGHT)["freeze"]["counts"]["S4_value_momentum"] == 1
    assert calls == [["2026-09-30"]] and frozen(shadow, "2026-10-27")["s4_source"] == "REUSED:2026-10-26"

    def broken(conn, tickers, month_ends):
        raise RuntimeError("fundamentals unavailable")
    monkeypatch.setattr(er, "_s4_eligibility", broken)
    nov = run(at(2026, 11, 3, 5, 20))                          # first session using the 10-30 month-end
    assert nov["freeze"]["status"] == "FROZEN" and nov["status"] == "ATTENTION"
    assert nov["freeze"]["s4_source"].startswith("UNAVAILABLE: RuntimeError")
    assert frozen(shadow, "2026-11-02")["s4_eligible"] is None


def test_a_code_change_is_recorded_once_and_does_not_stay_red(shadow, monkeypatch):
    run(FIRST_NIGHT)
    monkeypatch.setattr(f, "source_fingerprint", lambda: "changed")
    changed = run(BREAKOUT_NIGHT)
    assert changed["freeze"]["decision_sources_changed"] and changed["status"] == "ATTENTION"
    quiet = run(at(2026, 10, 29, 4, 20))
    assert not quiet["freeze"]["decision_sources_changed"] and quiet["status"] == "OK"
    assert count(shadow, f.CHECK_KIND, "AND payload->>'status'='DECISION_SOURCES_CHANGED'") == 1
    with shadow() as conn:
        assert f.summary(conn, now=at(2026, 10, 29, 12))["decision_code_versions"] == 2


def test_a_freeze_that_would_land_after_the_open_is_not_written(shadow):
    late = run(at(2026, 10, 27, 12, 35), clock_now=at(2026, 10, 27, 13, 31))
    assert late["freeze"]["status"] == "FREEZE_MISSED" and count(shadow, f.DECISION_KIND) == 0


def test_a_decision_recorded_after_its_deadline_is_late_and_never_traded(shadow):
    run(FIRST_NIGHT)
    late = run(at(2026, 10, 28, 12, 35), db_now=at(2026, 10, 28, 13, 31))   # the database records it late
    assert late["freeze"]["status"] == "FREEZE_LATE" and late["outcomes"]["invalid"] == ["2026-10-27"]
    assert run(WINDOW_DONE)["outcomes"]["scored"] == 0
    assert count(shadow, f.OUTCOME_KIND) == 0
    assert count(shadow, f.CHECK_KIND, "AND payload->>'status'='INVALID_PROSPECTIVE_TIMING'") == 1


def test_the_final_evaluation_waits_for_every_trade_in_the_window_and_is_written_once(shadow, monkeypatch):
    monkeypatch.setattr(f, "EVALUATION_MONTHS", ("2026-10", "2026-10"))
    run(FIRST_NIGHT)
    run(BREAKOUT_NIGHT)
    before = run(at(2026, 11, 25, 4, 20))                      # the October trade's window ends 11-25
    assert before["outcomes"]["unresolved_from"] == "2026-10-27" and not before["evaluation_final"]
    assert count(shadow, f.EVALUATION_KIND, "OR request_key=%s", (f"{f.FORWARD_ID}:final",)) == 0
    done = run(WINDOW_DONE)
    assert done["outcomes"]["scored"] == 1 and done["evaluation_final"]
    with shadow() as conn:
        final = f.final_evaluation(conn)
        status = f.summary(conn, now=at(2026, 11, 26, 12))
    s1 = final["strategies"]["S1_volume_breakout"]
    assert s1["trades"] == 1 and s1["verdict"] == "INSUFFICIENT_COVERAGE" and final["months"] == ["2026-10", "2026-10"]
    assert status["evaluation"]["final"] and status["monitoring"] is None
    run(at(2026, 11, 28, 4, 20))
    with shadow() as conn:
        assert f.final_evaluation(conn) == final


def test_protocol_and_replay_changes_stop_the_shadow(shadow, monkeypatch):
    assert run(FIRST_NIGHT)["status"] == "OK"
    monkeypatch.setattr(f, "PROTOCOL", {**f.PROTOCOL, "universe": ["VZ", "T"]})
    assert run(BREAKOUT_NIGHT)["reason_code"] == "FORWARD_PROTOCOL_CHANGED"
    monkeypatch.setattr(er, "PROTOCOL_SHA256", "c" * 64)
    assert run(BREAKOUT_NIGHT)["reason_code"] == "REPLAY_PROTOCOL_CHANGED"


def test_a_changed_shadow_behaviour_stops_before_anything_is_written(shadow, monkeypatch):
    monkeypatch.setattr(f, "SHADOW_BEHAVIOR_SHA256", "d" * 64)
    out = run(FIRST_NIGHT)
    assert out["status"] == "PROTOCOL_CHANGED" and out["reason_code"] == "SHADOW_BEHAVIOR_CHANGED"
    assert count(shadow, f.DECISION_KIND) == 0


def test_only_main_may_write(shadow, monkeypatch):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/feature")
    assert run(FIRST_NIGHT)["status"] == "REFUSED_NON_MAIN"
    assert count(shadow, f.DECISION_KIND) == 0


def test_a_partial_session_stays_visible_for_a_week(shadow):
    sql(shadow, "DELETE FROM prices_daily WHERE ticker='VZ' AND date='2026-10-26'")
    run(at(2026, 10, 27, 11, 50))                                  # final attempt: frozen without VZ
    replayed = run(at(2026, 10, 27, 13, 15))
    assert replayed["freeze"]["status"] == "REPLAYED" and replayed["status"] == "ATTENTION"
    assert replayed["sticky_attention"] == ["FROZEN_WITH_MISSING_BARS:2026-10-26"]
    assert "FROZEN_WITH_MISSING_BARS:2026-10-26" not in run(at(2026, 11, 4, 5, 20))["sticky_attention"]


def test_an_excluded_trade_is_recorded_with_provenance_and_reported(shadow, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    run(FIRST_NIGHT)
    run(BREAKOUT_NIGHT)
    sql(shadow, "DELETE FROM prices_daily WHERE ticker='VZ' AND date='2026-11-10'")
    expired = run(WINDOW_DONE)                       # the window has completed, 10+ sessions after the 11-10 gap
    assert expired["outcomes"]["excluded"] >= 1 and expired["status"] == "ATTENTION"
    assert any(a.startswith("OUTCOME_EXCLUDED_MISSING_BARS:2026-10-27:S1_volume_breakout:VZ")
               for a in expired["sticky_attention"])
    with shadow() as conn:
        trade = research_store.get(conn, f.OUTCOME_KIND, f.outcome_key("S1_volume_breakout", "VZ", "2026-10-27"))
    assert trade["payload"]["status"] == "EXCLUDED_MISSING_BARS"
    assert trade["payload"]["code"]["git_sha"] == "abc123" and trade["payload"]["code"]["decision_sources_sha256"]


def test_a_late_freeze_is_listed_once(shadow):
    run(FIRST_NIGHT)
    late = run(at(2026, 10, 28, 12, 35), db_now=at(2026, 10, 28, 13, 31))
    assert late["freeze"]["status"] == "FREEZE_LATE" and late["outcomes"]["invalid"] == ["2026-10-27"]
    with shadow() as conn:   # both the late marker and the invalid-timing check exist; one is listed
        assert f.summary(conn, now=at(2026, 10, 28, 14, 0))["sticky_attention"] == ["FREEZE_LATE:2026-10-27"]
    assert count(shadow, f.CHECK_KIND, "AND payload->>'status'='INVALID_PROSPECTIVE_TIMING'") == 1


def test_a_moved_tracked_file_is_reported_nightly_and_never_costs_a_session(shadow, monkeypatch):
    run(FIRST_NIGHT)
    monkeypatch.setattr(f, "DECISION_SOURCES", (*f.DECISION_SOURCES, "backtest/moved_away.py"))
    nightly = run(BREAKOUT_NIGHT)
    assert nightly["freeze"]["status"] == "FROZEN" and nightly["freeze"]["decision_sources_changed"]
    assert nightly["missing_sources"] == ["backtest/moved_away.py"] and nightly["status"] == "ATTENTION"
    with shadow() as conn:
        check = research_store.get(conn, f.CHECK_KIND, f"{f.FORWARD_ID}:2026-10-27:DECISION_SOURCES_CHANGED")
    assert check["payload"]["missing_sources"] == ["backtest/moved_away.py"]
