from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from stock_machine import scheduled_operations as ops, automation_api


def at(value):
    return datetime.fromisoformat(value)


@pytest.mark.parametrize(
    "instant,cron,due",
    [
        ("2026-10-07T21:30:00+00:00", "30 21 * * 1-5", True),
        ("2026-10-07T22:30:00+00:00", "30 22 * * 1-5", False),
        ("2026-11-05T21:30:00+00:00", "30 21 * * 1-5", False),
        ("2026-11-05T22:30:00+00:00", "30 22 * * 1-5", True),
        ("2026-10-07T19:30:00+00:00", "30 21 * * 1-5", False),
        ("2026-11-26T22:30:00+00:00", "30 22 * * 1-5", False),
        ("2026-11-27T22:30:00+00:00", "30 22 * * 1-5", True),
        ("2026-10-10T21:30:00+00:00", "30 21 * * 1-5", False),
    ],
)
def test_ingestion_clock_dst_holidays_and_early_close(instant, cron, due):
    assert ops.schedule("ingestion", at(instant), workflow_cron=cron)["due"] is due


@pytest.mark.parametrize(
    "instant,stage,due",
    [
        ("2026-10-07T22:10:00+00:00", "learning", True),
        ("2026-10-08T00:10:00+00:00", "learning", True),
        ("2026-10-08T02:10:00+00:00", "learning", True),
        ("2026-11-06T03:10:00+00:00", "learning", True),
        ("2026-10-07T23:10:00+00:00", "learning", False),
        ("2026-10-08T03:12:00+00:00", "paper", False),
        ("2026-10-08T02:52:00+00:00", "paper", True),
        ("2026-10-08T12:15:00+00:00", "progress", True),
        ("2026-11-05T13:15:00+00:00", "progress", True),
        ("2026-10-10T12:15:00+00:00", "progress", False),
    ],
)
def test_stage_windows(instant, stage, due):
    assert ops.schedule(stage, at(instant))["due"] is due


def test_stable_slot_and_no_database_when_not_due(monkeypatch):
    a = ops.schedule("paper", at("2026-10-07T21:12:00+00:00"))
    b = ops.schedule("paper", at("2026-10-07T21:19:00+00:00"))
    assert a["key"] == b["key"]
    monkeypatch.setattr(
        ops.db, "connect", lambda: pytest.fail("out-of-window DB access")
    )
    assert (
        ops.run("learning", now=at("2026-10-07T19:10:00+00:00"))["status"] == "SKIPPED"
    )
    with pytest.raises(ValueError):
        ops.schedule("paper", datetime(2026, 10, 7))


def test_paper_pause_and_mode_are_respected(monkeypatch):
    from stock_machine import agent_trading

    monkeypatch.setattr(
        agent_trading, "process_pending", lambda **kw: pytest.fail("paper ran")
    )
    monkeypatch.setattr(ops.store, "controls", lambda: {"capture_enabled": False})
    assert ops.paper_operation()["reason"] == "CAPTURE_PAUSED"
    monkeypatch.setattr(ops.store, "controls", lambda: {"capture_enabled": True})
    monkeypatch.setattr(agent_trading, "get_mode", lambda: {"mode": "RESEARCH"})
    assert ops.paper_operation()["reason"] == "RESEARCH_MODE"


def test_learning_failure_does_not_stop_shadow(monkeypatch):
    from stock_machine.agent_intelligence import outcomes, shadow

    def fail(**kw):
        raise RuntimeError("private provider details")

    monkeypatch.setattr(outcomes, "score_matured", fail)
    monkeypatch.setattr(
        shadow, "score_matured", lambda **kw: {"status": "OK", "scored": 3}
    )
    result = ops.learning_operation()
    assert result["status"] == "ATTENTION" and result["shadow_scored"] == 3
    assert result["paper_reason_code"] == "RuntimeError"
    assert "private provider" not in str(result)
    assert result["promotion"] == "NOT_AUTHORIZED"


def test_cron_auth_stage_boundary_and_failure(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "scheduled-fixture-secret-123456789")
    monkeypatch.delenv("STOCK_MACHINE_ADMIN_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(automation_api.router)
    client = TestClient(app)
    calls = []
    monkeypatch.setattr(
        ops,
        "run",
        lambda stage: calls.append(stage)
        or {"status": "FAILED", "reason_code": "RuntimeError"},
    )
    assert client.get("/api/admin/agent/cron/learning").status_code == 401
    headers = {"Authorization": "Bearer scheduled-fixture-secret-123456789"}
    assert (
        client.get("/api/admin/agent/cron/ingestion", headers=headers).status_code
        == 404
    )
    assert (
        client.get("/api/admin/agent/cron/learning", headers=headers).status_code == 503
    )
    assert calls == ["learning"]
