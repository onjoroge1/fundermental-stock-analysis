"""Forward tracker against real Postgres: freeze, replay, score, miss, timing."""

import math
from datetime import datetime, timezone

import pytest

from stock_machine import research_store
from stock_machine.agent_intelligence import fair_value_forward as f
from stock_machine.market_calendar import session_dates
from tests.test_research_postgres import pg  # noqa: F401  (Postgres fixture)

DAYS = session_dates("2025-06-02", "2027-06-30")


def seed(conn, ticker, drift, start=100.0):
    rows, price = [], start
    for i, day in enumerate(DAYS):
        price *= 1 + drift + 0.006 * math.sin(i / 4)
        rows.append((ticker, day, price, price * 1.01, price * 0.99, price, price, 1_000_000))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO prices_daily VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", rows)


@pytest.fixture
def tracker(pg, monkeypatch):  # noqa: F811
    monkeypatch.setattr(f, "PROTOCOL", {**f.PROTOCOL, "universe": ["VZ"]})
    with pg() as conn:
        seed(conn, "VZ", 0.0008)
        seed(conn, "SPY", 0.0004, 400.0)
        conn.commit()
    return pg


def count(pg, kind):  # noqa: F811
    with pg() as conn:
        return conn.execute("SELECT count(*) FROM research_evidence_records WHERE kind=%s", (kind,)).fetchone()[0]


NIGHT = datetime(2026, 10, 31, 4, 20, tzinfo=timezone.utc)   # after the 2026-10-30 close


def test_freeze_replay_and_score_one_month(tracker):
    first = f.run_daily(now=NIGHT)
    assert first["status"] == "OK" and first["freeze"]["status"] == "FROZEN"
    assert first["freeze"]["counts"] == {"LONG": 0, "SHORT": 0, "FLAT": 1, "NO_DECISION": 0}  # no filings: never solid
    again = f.run_daily(now=datetime(2026, 11, 2, 11, 50, tzinfo=timezone.utc))
    assert again["freeze"]["status"] == "REPLAYED"
    assert count(tracker, f.DECISION_KIND) == 1 and count(tracker, f.PROTOCOL_KIND) == 1
    with tracker() as conn:
        status = f.summary(conn, now=datetime(2026, 11, 3, 12, tzinfo=timezone.utc))
    assert status["protocol_registered"] and not status["freeze_missing"]
    assert status["frozen_months"][0]["due_session"] == "2027-04-27"
    assert status["evaluation"]["status"] == "PENDING_EVIDENCE"
    # Not yet due: nothing scored.
    assert f.run_daily(now=datetime(2027, 4, 27, 12, tzinfo=timezone.utc))["outcomes"]["scored"] == []
    matured = f.run_daily(now=datetime(2027, 4, 28, 4, 20, tzinfo=timezone.utc))
    assert matured["outcomes"]["scored"] == ["2026-10-30"]
    with tracker() as conn:
        out = research_store.get(conn, f.OUTCOME_KIND, f"{f.FORWARD_ID}:2026-10-30")["payload"]
    assert out["status"] == "SCORED" and out["monthly_mean"] == 0.0 and out["active"] is False
    assert f.run_daily(now=datetime(2027, 4, 29, 4, 20, tzinfo=timezone.utc))["outcomes"]["scored"] == []
    assert count(tracker, f.OUTCOME_KIND) == 1


def test_a_missed_month_is_recorded_once_and_never_reconstructed(tracker):
    late = datetime(2026, 11, 3, 4, 20, tzinfo=timezone.utc)      # after the 2026-11-02 open
    missed = f.run_daily(now=late)
    assert missed["status"] == "ATTENTION" and missed["freeze"]["status"] == "FREEZE_MISSED"
    later = f.run_daily(now=datetime(2026, 11, 4, 4, 20, tzinfo=timezone.utc))
    assert later["status"] == "OK" and later["freeze"]["status"] == "MISSED_ALREADY_RECORDED"
    assert count(tracker, f.DECISION_KIND) == 0 and count(tracker, f.CHECK_KIND) == 1
    with tracker() as conn:
        status = f.summary(conn, now=datetime(2026, 11, 4, 12, tzinfo=timezone.utc))
    assert status["freeze_missing"] and status["missed_months"] == ["2026-10-30"]


def test_missing_bars_wait_for_data_until_the_final_attempt(tracker):
    with tracker() as conn:
        conn.execute("DELETE FROM prices_daily WHERE ticker='VZ' AND date='2026-10-30'")
        conn.commit()
    night = f.run_daily(now=NIGHT)
    assert night["status"] == "ATTENTION" and night["freeze"]["status"] == "FREEZE_PENDING_DATA"
    assert night["freeze"]["missing"] == ["VZ"] and count(tracker, f.DECISION_KIND) == 0
    final = f.run_daily(now=datetime(2026, 11, 2, 11, 50, tzinfo=timezone.utc))   # < 4h before the open
    assert final["freeze"]["status"] == "FROZEN"
    assert final["freeze"]["missing_bars_at_freeze"] == ["VZ"]
    assert final["freeze"]["counts"]["NO_DECISION"] == 1


def test_a_late_recorded_decision_is_never_scored(tracker):
    f.run_daily(now=NIGHT)  # registers the protocol
    with tracker() as conn:
        protocol = research_store.get(conn, f.PROTOCOL_KIND, f.FORWARD_ID)
        payload = f.decision_payload("2026-11-30", [], protocol=protocol,
                                     created_at=datetime(2026, 12, 1, 15, tzinfo=timezone.utc), missing=[])
        research_store.save(conn, f.DECISION_KIND, f"{f.FORWARD_ID}:2026-11-30", payload)
        conn.commit()
    result = f.run_daily(now=datetime(2027, 6, 1, 4, 20, tzinfo=timezone.utc))
    assert "2026-11-30" in result["outcomes"]["invalid"] and result["status"] == "ATTENTION"
    with tracker() as conn:
        assert research_store.get(conn, f.OUTCOME_KIND, f"{f.FORWARD_ID}:2026-11-30")["payload"]["status"] == (
            "INVALID_PROSPECTIVE_TIMING")


def test_a_changed_protocol_refuses_to_run(tracker, monkeypatch):
    f.run_daily(now=NIGHT)
    monkeypatch.setattr(f, "PROTOCOL", {**f.PROTOCOL, "universe": ["VZ", "AAPL"]})
    assert f.run_daily(now=NIGHT)["status"] == "PROTOCOL_CHANGED"
    monkeypatch.setattr(f, "REPLAY_PROTOCOL_SHA256", "0" * 64)
    assert f.run_daily(now=NIGHT)["reason_code"] == "REPLAY_PROTOCOL_CHANGED"
