"""Agent Trading v1 contracts: deterministic paper only, never broker execution."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stock_machine import agent_trading
from stock_machine.admin_panel import api, security, store

ORIGIN = "https://testserver"
HEADERS = {"Origin": ORIGIN, "X-CSRF-Token": "fixture-csrf"}


def test_classification_to_side_is_fixed():
    assert agent_trading.desired_side("ATTRACTIVE") == "LONG"
    assert agent_trading.desired_side("UNATTRACTIVE") == "SHORT"
    assert agent_trading.desired_side("WATCH") == "FLAT"
    assert agent_trading.desired_side("INSUFFICIENT_DATA") == "FLAT"
    assert agent_trading.desired_side(None) == "FLAT"


def test_action_matrix_flattens_before_reversal():
    assert agent_trading.deterministic_action("LONG", None) == "OPEN_LONG"
    assert agent_trading.deterministic_action("SHORT", None) == "OPEN_SHORT"
    assert agent_trading.deterministic_action("FLAT", None) == "NO_TRADE"
    assert agent_trading.deterministic_action("LONG", "LONG") == "HOLD"
    assert agent_trading.deterministic_action("SHORT", "SHORT") == "HOLD"
    assert agent_trading.deterministic_action("SHORT", "LONG") == "CLOSE"
    assert agent_trading.deterministic_action("LONG", "SHORT") == "CLOSE"
    assert agent_trading.deterministic_action("FLAT", "LONG") == "CLOSE"


def test_fixed_risk_budget_caps_position_gross_and_count():
    target, blockers = agent_trading.position_budget(100_000, 0, 0)
    assert target == 10_000 and blockers == []
    target, blockers = agent_trading.position_budget(100_000, 45_000, 4)
    assert target == 5_000 and blockers == []
    target, blockers = agent_trading.position_budget(100_000, 49_700, 4)
    assert target == 300 and "INSUFFICIENT_GROSS_CAPACITY" in blockers
    _, blockers = agent_trading.position_budget(100_000, 10_000, 5)
    assert "MAX_OPEN_POSITIONS_REACHED" in blockers


def test_v1_has_no_live_mode_or_broker_client_surface():
    source = Path(agent_trading.__file__).read_text()
    assert "IBKR" not in source and "submit_order" not in source and "place_order" not in source
    assert "LIVE" not in agent_trading.SCHEMA
    assert agent_trading.FILL_COST_BPS == 10.0
    assert agent_trading.MAX_POSITION_PCT == 0.10
    assert agent_trading.MAX_GROSS_PCT == 0.50


def test_mode_read_defaults_to_research_without_creating_schema(monkeypatch):
    class MissingConn:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, *args, **kwargs):
            import psycopg
            raise psycopg.errors.UndefinedTable("missing")
        def rollback(self): self.rolled_back = True
    monkeypatch.setattr(agent_trading.db, "connect", lambda: MissingConn())
    assert agent_trading.get_mode() == {
        "mode": "RESEARCH", "version": 0, "updated_at": None,
        "initialized": False, "broker_submission": False,
        "live_trading_available": False,
    }


def test_process_decision_skips_cleanly_outside_paper(monkeypatch):
    monkeypatch.setattr(agent_trading, "get_mode", lambda: {
        "mode": "RESEARCH", "version": 0, "updated_at": None,
        "initialized": False, "broker_submission": False,
        "live_trading_available": False,
    })
    value = agent_trading.process_decision({"ticker": "AAPL", "decision_id": "fixture"})
    assert value["status"] == "SKIPPED"
    assert value["broker_submission"] is False
    assert value["execution_mode"] == "RESEARCH"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    app = FastAPI(); app.include_router(api.router)
    monkeypatch.setattr(store, "session", lambda *a, **kw: {
        "username": "admin", "csrf_token": "fixture-csrf", "must_change_password": False
    })
    return TestClient(app, base_url=ORIGIN)


def test_admin_mode_route_accepts_only_research_or_paper(client, monkeypatch):
    calls = []
    monkeypatch.setattr(store, "set_trading_mode", lambda actor, mode, version, reason:
                        calls.append((actor, mode, version, reason)) or {
                            "mode": mode, "version": 2, "broker_submission": False})
    r = client.post("/api/operator/trading-mode", headers=HEADERS,
                    json={"mode":"PAPER","expected_version":0,"reason":"paper test"})
    assert r.status_code == 200 and calls == [("admin", "PAPER", None, "paper test")]
    r = client.post("/api/operator/trading-mode", headers=HEADERS,
                    json={"mode":"LIVE","expected_version":2,"reason":"not allowed"})
    assert r.status_code == 400


def test_admin_surface_exposes_no_live_or_order_endpoint(client):
    for path in ("/api/operator/live", "/api/operator/orders", "/api/operator/execute",
                 "/api/operator/broker", "/api/operator/fills"):
        assert client.post(path, headers=HEADERS, json={}).status_code == 404
