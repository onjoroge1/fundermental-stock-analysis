from datetime import datetime, timezone
from tests.test_research_postgres import pg
from tests.test_technical_setups import bars
from stock_machine import db, research_store
from stock_machine.agent_intelligence import (
    technical_setup_store as store,
    technical_setups as t,
)


def test_daily_atomic_weights_inputs_snapshots_replay_and_read_only_summary(
    pg, monkeypatch
):
    monkeypatch.setattr(store, "AGENT_UNIVERSE", ("VZ",))
    rows = bars()
    monkeypatch.setattr(db, "fetch_prices", lambda *args: rows)
    now = datetime(2026, 10, 7, 23, 40, tzinfo=timezone.utc)
    result = store.run_daily(now=now)
    assert result["status"] == "OK"
    repeated = store.run_daily(now=now)
    assert all(r["replayed"] for r in repeated["results"])
    with pg() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM research_evidence_records WHERE kind=%s",
                (store.RUN,),
            ).fetchone()[0]
            == 3
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM research_evidence_records WHERE kind=%s",
                (store.SNAPSHOT,),
            ).fetchone()[0]
            == 3
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM research_evidence_records WHERE kind=%s",
                (store.INPUT,),
            ).fetchone()[0]
            == 1
        )
        r = research_store.latest(conn, store.RUN, "VZ")["payload"]
        assert r["source"] == "HISTORICAL_RECONSTRUCTION_CURRENT_VINTAGE"
        assert r["current_train_max_due"] <= r["as_of"]
        assert "oos_predictions" not in r and r["oos_predictions_hash"]
    summary = store.summary()
    assert len(summary["rows"]) == 3 and summary["promotion"] == "NOT_AUTHORIZED"


def test_missing_bars_visible_and_failed_stage_can_retry(pg, monkeypatch):
    monkeypatch.setattr(store, "AGENT_UNIVERSE", ("VZ",))
    monkeypatch.setattr(db, "fetch_prices", lambda *args: [])
    now = datetime(2026, 10, 7, 23, 40, tzinfo=timezone.utc)
    assert store.run_daily(now=now)["status"] == "ATTENTION"
    monkeypatch.setattr(db, "fetch_prices", lambda *args: bars())
    assert store.run_daily(now=now)["status"] == "OK"


def test_prospective_outcome_uses_frozen_action_and_complete_next_close_path(
    pg, monkeypatch
):
    rows = bars()
    sample, _ = t.examples(rows, rows[-1]["date"], 5)
    s = sample[-1]
    monkeypatch.setattr(db, "fetch_prices", lambda *args: rows)
    payload = {
        "ticker": "VZ",
        "horizon_sessions": 5,
        "entry_session": s["entry"],
        "due_session": s["due"],
        "action": -1,
        "weight_version": "frozen",
    }
    with pg() as conn:
        research_store.save(conn, store.SNAPSHOT, "fixture", payload, "VZ")
    assert store.score_pending(rows[-1]["date"])["scored"] == 1
    assert store.score_pending(rows[-1]["date"])["scored"] == 0
    with pg() as conn:
        outcome = research_store.get(conn, store.OUTCOME, "fixture")["payload"]
        assert outcome["net_return"] == -s["return"] - 0.002
        assert (
            outcome["weight_version"] == "frozen"
            and outcome["source"] == "PROSPECTIVE_FROZEN_SETUP"
        )


def test_missing_prospective_path_has_durable_retry_evidence(pg, monkeypatch):
    monkeypatch.setattr(db, "fetch_prices", lambda *args: [])
    payload = {
        "ticker": "VZ",
        "horizon_sessions": 5,
        "entry_session": "2026-09-28",
        "due_session": "2026-10-05",
        "action": 1,
        "weight_version": "frozen",
    }
    with pg() as conn:
        research_store.save(conn, store.SNAPSHOT, "missing", payload, "VZ")
    assert store.score_pending("2026-10-07")["blocked"] == 1
    assert store.score_pending("2026-10-07")["blocked"] == 1
    with pg() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM research_evidence_records WHERE kind=%s",
                (store.CHECK,),
            ).fetchone()[0]
            == 1
        )
        assert research_store.get(conn, store.OUTCOME, "missing") is None
