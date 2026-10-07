from datetime import datetime
from psycopg.types.json import Jsonb
from tests.test_research_postgres import pg
from stock_machine import scheduled_operations as ops, automation

NOW = datetime.fromisoformat("2026-10-07T22:10:00+00:00")


def test_receipt_replay_failure_retry_and_unlock(pg):
    calls = []

    def success():
        calls.append(1)
        return {
            "status": "OK",
            "shadow_scored": 2,
            "stage_receipts": [{"do_not_embed": "history"}],
        }

    result = ops.run("learning", now=NOW, operation=success)
    assert result["shadow_scored"] == 2 and not result["replayed"]
    assert "stage_receipts" not in result
    assert ops.run("learning", now=NOW, operation=success)["replayed"]
    assert len(calls) == 1
    with pg() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM operator_audit WHERE event LIKE 'AGENT_STAGE_%'"
            ).fetchone()[0]
            == 2
        )
    later = datetime.fromisoformat("2026-10-08T00:10:00+00:00")

    def fail():
        raise RuntimeError("do not leak")

    assert ops.run("learning", now=later, operation=fail)["status"] == "FAILED"
    assert ops.run("learning", now=later, operation=success)["status"] == "OK"
    assert len(calls) == 2


def test_concurrent_delivery_cannot_enter_locked_stage(pg):
    key = ops.schedule("learning", NOW)["key"]
    with pg() as conn:
        conn.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (key,))
        try:
            result = ops.run(
                "learning",
                now=NOW,
                operation=lambda: (_ for _ in ()).throw(AssertionError("entered")),
            )
            assert result["status"] == "BUSY"
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (key,))
    assert (
        ops.run("learning", now=NOW, operation=lambda: {"status": "OK"})["status"]
        == "OK"
    )


def test_progress_distinguishes_unfinished_receipt_and_coverage(pg, monkeypatch):
    monkeypatch.setattr(ops, "latest_completed_session", lambda: "2026-10-07")
    with pg() as conn:
        ops.store.audit(
            conn, "fixture", "AGENT_STAGE_STARTED", ops.schedule("learning", NOW)
        )
    value = ops.progress_report()
    assert value["status"] == "ATTENTION"
    assert value["stage_receipts"][0]["status"] == "UNFINISHED"
    assert value["prices_current"] == 0 and value["forecasts_current"] == 0
    assert value["universe_count"] == 54 and value["research_status"] == "COLLECTING"
    with pg() as conn:
        assert conn.execute("SELECT count(*) FROM operator_audit").fetchone()[0] == 1


def test_research_waits_for_current_forecast_and_manifest(pg):
    session = "2026-10-07"
    with pg() as conn:
        conn.execute(
            "INSERT INTO prices_daily(ticker,date,close,adj_close) VALUES ('VZ',%s,100,100)",
            (session,),
        )
        conn.execute(
            """INSERT INTO dataset_snapshots(snapshot_id,ticker,dataset,content_hash,row_count,max_record_date,status)
            VALUES ('fixture','VZ','prices','hash',1,%s,'PASS')""",
            (session,),
        )
        assert automation.choose_agent_ticker(conn, session) is None
        conn.execute(
            """INSERT INTO prediction_forecasts(forecast_id,ticker,as_of,model_version,status,payload)
            VALUES ('forecast-fixture','VZ',%s,'fixture','OK',%s)""",
            (session, Jsonb({})),
        )
        assert automation.choose_agent_ticker(conn, session) == "VZ"
        conn.execute("UPDATE prediction_forecasts SET status='FAILED'")
        assert automation.choose_agent_ticker(conn, session) is None


def test_progress_only_counts_unscored_shadow_targets(pg, monkeypatch):
    from stock_machine import research_store

    monkeypatch.setattr(ops, "latest_completed_session", lambda: "2026-10-07")
    with pg() as conn:
        for key, horizon, due in [
            ("due", 5, "2026-10-07"),
            ("future", 10, "2026-10-14"),
            ("done", 5, "2026-10-06"),
        ]:
            research_store.save(
                conn,
                "AGENT_SHADOW_SNAPSHOT_V1",
                key,
                {"horizon_sessions": horizon, "due_session": due},
                "VZ",
            )
        research_store.save(
            conn, "AGENT_SHADOW_OUTCOME_V1", "done", {"status": "SCORED"}, "VZ"
        )
    result = ops.progress_report()
    assert result["shadow_pending_count"] == 2 and result["shadow_due_count"] == 1
    assert result["first_five_target_session"] == "2026-10-07"
    assert result["shadow_pending"] == [
        {"horizon": 5, "pending": 1, "due": 1, "first_target_session": "2026-10-07"},
        {"horizon": 10, "pending": 1, "due": 0, "first_target_session": "2026-10-14"},
    ]
