import pytest
from fastapi.testclient import TestClient

import stock_machine.control_plane as cp


def test_ticker_normalization_and_validation():
    assert cp.normalize_ticker(" hims ") == "HIMS"
    assert cp.normalize_ticker("BRK.B") == "BRK.B"
    with pytest.raises(ValueError):
        cp.normalize_ticker("bad ticker")
    with pytest.raises(ValueError):
        cp.normalize_ticker(";DROP TABLE")


def test_default_idempotency_key_is_stable_for_same_request():
    a = cp.default_idempotency_key("ticker_refresh", "HIMS", {"x": 1})
    b = cp.default_idempotency_key("ticker_refresh", "HIMS", {"x": 1})
    c = cp.default_idempotency_key("ticker_refresh", "HIMS", {"x": 2})
    assert a == b
    assert a != c


def test_execute_dispatches_only_allowlisted_job_types(monkeypatch):
    monkeypatch.setattr(cp, "_ticker_refresh", lambda ticker: {"ticker": ticker})
    assert cp.execute({
        "job_type": "ticker_refresh", "ticker": "HIMS", "payload": {}
    }) == {"ticker": "HIMS"}
    with pytest.raises(ValueError, match="unsupported job_type"):
        cp.execute({"job_type": "shell", "ticker": None, "payload": {}})


def test_admin_routes_fail_closed_when_secret_is_missing(monkeypatch):
    monkeypatch.delenv("STOCK_MACHINE_ADMIN_TOKEN", raising=False)
    from stock_machine.webapp_ops import app
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/api/admin/jobs")
    assert response.status_code == 503


def test_admin_routes_reject_wrong_bearer(monkeypatch):
    monkeypatch.setenv(
        "STOCK_MACHINE_ADMIN_TOKEN", "0123456789abcdef0123456789abcdef"
    )
    from stock_machine.webapp_ops import app
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/api/admin/jobs", headers={"Authorization": "Bearer wrong-token"}
    )
    assert response.status_code == 401


def test_processor_accepts_cron_secret_without_admin_secret(monkeypatch):
    monkeypatch.delenv("STOCK_MACHINE_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("CRON_SECRET", "abcdef0123456789abcdef0123456789")
    monkeypatch.setattr(cp, "process_one", lambda: {"status": "IDLE"})
    from stock_machine.webapp_ops import app
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/api/admin/jobs/process",
        headers={"Authorization": "Bearer abcdef0123456789abcdef0123456789"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "IDLE"
