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


def at(*args):
    return datetime(*args, tzinfo=timezone.utc)


def no_filings(ciks, session):
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


def delete_bar(pg, ticker, day):  # noqa: F811
    with pg() as conn:
        conn.execute("DELETE FROM prices_daily WHERE ticker=%s AND date=%s", (ticker, day))
        conn.commit()


def test_freeze_replay_and_one_trade_end_to_end(shadow):
    first = run(at(2026, 10, 22, 4, 20))                       # after the 2026-10-21 close
    assert first["status"] == "OK" and first["freeze"]["status"] == "FROZEN"
    assert first["freeze"]["counts"] == {s: 0 for s in f.STRATEGIES}
    breakout = run(at(2026, 10, 23, 4, 20))
    assert breakout["freeze"]["counts"]["S1_volume_breakout"] == 1
    assert breakout["freeze"]["counts"]["S4_value_momentum"] == 0      # no filings: never a solid company
    assert run(at(2026, 10, 23, 12, 30))["freeze"]["status"] == "REPLAYED"
    assert count(shadow, f.DECISION_KIND) == 2 and count(shadow, f.PROTOCOL_KIND, "OR request_key=%s",
                                                         (f.FORWARD_ID,)) == 1
    with shadow() as conn:
        view = f.summary(conn, now=at(2026, 10, 23, 12, 45), ticker="VZ")
    assert view["open_trades"] == [{"ticker": "VZ", "strategy": "S1_volume_breakout", "signal": "2026-10-22",
                                    "entry": "2026-10-23", "window_ends": "2026-11-20"}]
    assert view["latest_decision"]["rows"][0]["flags"]["S1_volume_breakout"] is True
    assert view["use"] == "INFORMATION_ONLY" and view["evaluation"] == {"status": "PENDING", "final": False}
    assert "verdict" not in view["monitoring"]["strategies"]["S1_volume_breakout"]
    matured = run(at(2026, 11, 21, 4, 20))                     # the window completes at the 11-20 close
    assert matured["outcomes"]["scored"] == 1 and matured["status"] == "ATTENTION"   # sessions in between missed
    with shadow() as conn:
        trade = research_store.get(conn, f.OUTCOME_KIND, f.outcome_key("S1_volume_breakout", "VZ", "2026-10-22"))
        assert trade["payload"]["status"] == "SCORED" and trade["payload"]["exit"] == "2026-11-20"
        # The fair-value tracker shares the evidence kinds but never sees these records.
        assert fvf.decisions(conn) == [] and fvf.outcomes(conn) == []
        assert fvf.summary(conn, now=at(2026, 11, 21, 12))["frozen_months"] == []
    assert run(at(2026, 11, 24, 4, 20))["outcomes"]["scored"] == 0


def test_missed_sessions_are_recorded_once_and_never_reconstructed(shadow):
    late = run(at(2026, 10, 22, 14, 0))                        # after the 10-22 open
    assert late["freeze"]["status"] == "FREEZE_MISSED" and late["status"] == "ATTENTION"
    back = run(at(2026, 10, 27, 4, 20))                        # after the 10-26 close
    assert back["freeze"]["status"] == "FROZEN" and back["freeze"]["decision_session"] == "2026-10-26"
    assert back["freeze"]["newly_missed"] == ["2026-10-22", "2026-10-23"]
    assert "FREEZE_MISSED:2026-10-21" in back["sticky_attention"]
    with shadow() as conn:
        status = f.summary(conn, now=at(2026, 10, 27, 12))
    assert status["missed_sessions"] == ["2026-10-21", "2026-10-22", "2026-10-23"] and status["frozen_sessions"] == 1


def test_missing_bars_wait_for_the_final_attempt(shadow):
    delete_bar(shadow, "VZ", "2026-10-21")
    night = run(at(2026, 10, 22, 4, 20))
    assert night["freeze"]["status"] == "FREEZE_PENDING_DATA" and night["freeze"]["missing"] == ["VZ"]
    final = run(at(2026, 10, 22, 10, 45))                      # within 4h of the 13:30 UTC open
    assert final["freeze"]["status"] == "FROZEN" and final["freeze"]["missing_bars_at_freeze"] == ["VZ"]
    with shadow() as conn:
        frozen = research_store.get(conn, f.DECISION_KIND, f"{f.FORWARD_ID}:2026-10-21")["payload"]
    assert frozen["with_bar"] == [] and frozen["rows"] == []


def test_unread_filings_wait_for_the_final_attempt_then_leave_s3_undecided(shadow):
    assert run(at(2026, 10, 22, 4, 20))["freeze"]["status"] == "FROZEN"

    def sec_down(ciks, session):
        return {}, sorted(ciks)
    night = run(at(2026, 10, 23, 4, 20), fetch=sec_down)
    assert night["freeze"]["status"] == "FREEZE_PENDING_DATA" and night["freeze"]["sec_unavailable"] == ["VZ"]
    final = run(at(2026, 10, 23, 11, 45), fetch=sec_down)
    assert final["freeze"]["status"] == "FROZEN" and final["freeze"]["sec_unavailable"] == ["VZ"]
    assert final["status"] == "ATTENTION"
    with shadow() as conn:
        row, = research_store.get(conn, f.DECISION_KIND, f"{f.FORWARD_ID}:2026-10-22")["payload"]["rows"]
    assert row["flags"]["S1_volume_breakout"] is True and row["flags"]["S3_post_earnings"] is None


def test_without_a_spy_bar_the_session_is_never_frozen(shadow):
    delete_bar(shadow, "SPY", "2026-10-21")
    assert run(at(2026, 10, 22, 12, 30))["freeze"]["status"] == "FREEZE_PENDING_DATA"
    assert run(at(2026, 10, 22, 14, 0))["freeze"]["status"] == "FREEZE_MISSED"
    assert count(shadow, f.DECISION_KIND) == 0


def test_a_freeze_that_would_land_after_the_open_is_not_written(shadow):
    late = run(at(2026, 10, 22, 12, 30), clock_now=at(2026, 10, 22, 13, 31))
    assert late["freeze"]["status"] == "FREEZE_MISSED" and count(shadow, f.DECISION_KIND) == 0


def test_a_decision_recorded_after_its_deadline_is_late_and_never_traded(shadow):
    run(at(2026, 10, 22, 4, 20))
    late = run(at(2026, 10, 23, 12, 30), db_now=at(2026, 10, 23, 13, 31))   # the database records it late
    assert late["freeze"]["status"] == "FREEZE_LATE" and late["outcomes"]["invalid"] == ["2026-10-22"]
    assert run(at(2026, 11, 21, 4, 20))["outcomes"]["scored"] == 0
    assert count(shadow, f.OUTCOME_KIND) == 0
    assert count(shadow, f.CHECK_KIND, "AND payload->>'status'='INVALID_PROSPECTIVE_TIMING'") == 1


def test_protocol_and_replay_changes_stop_the_shadow(shadow, monkeypatch):
    assert run(at(2026, 10, 22, 4, 20))["status"] == "OK"
    monkeypatch.setattr(f, "PROTOCOL", {**f.PROTOCOL, "universe": ["VZ", "T"]})
    assert run(at(2026, 10, 23, 4, 20))["reason_code"] == "FORWARD_PROTOCOL_CHANGED"
    monkeypatch.setattr(er, "PROTOCOL_SHA256", "c" * 64)
    assert run(at(2026, 10, 23, 4, 20))["reason_code"] == "REPLAY_PROTOCOL_CHANGED"


def test_only_main_may_write(shadow, monkeypatch):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/feature")
    assert run(at(2026, 10, 22, 4, 20))["status"] == "REFUSED_NON_MAIN"
    assert count(shadow, f.DECISION_KIND) == 0
