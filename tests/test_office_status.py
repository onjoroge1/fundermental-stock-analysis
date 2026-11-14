"""The owner's read-only office status: it needs the owner's session, it only reads, and it shows no secret."""
import inspect
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stock_machine.admin_panel import api, security, store
from stock_machine.office import status, tasks

ORIGIN = "https://testserver"
ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "sk-ant-SENTINEL-do-not-leak-51c2"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app, base_url=ORIGIN)


def test_a_request_without_the_owners_session_gets_nothing(client, monkeypatch):
    monkeypatch.setattr(store, "session",
                        lambda *a, **kw: (_ for _ in ()).throw(security.PanelError("LOGIN_REQUIRED", 401)))
    monkeypatch.setattr(status, "summary", lambda: pytest.fail("status was read without a session"))
    reply = client.get("/api/operator/office")
    assert reply.status_code == 401 and reply.json() == {"error_code": "LOGIN_REQUIRED"}


def test_the_signed_in_owner_reads_the_summary_and_cannot_write_through_this_route(client, monkeypatch):
    monkeypatch.setattr(store, "session", lambda *a, **kw: {"username": "admin", "must_change_password": False})
    monkeypatch.setattr(status, "summary", lambda: {"schema": "READY", "tasks": {"QUEUED": 2}})
    reply = client.get("/api/operator/office")
    assert reply.status_code == 200 and reply.json() == {"schema": "READY", "tasks": {"QUEUED": 2}}
    assert reply.headers["cache-control"] == "no-store"
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/api/operator/office").status_code == 405


def test_a_storage_failure_is_reported_as_a_code_not_a_message(client, monkeypatch):
    monkeypatch.setattr(store, "session", lambda *a, **kw: {"username": "admin", "must_change_password": False})

    def broken():
        raise RuntimeError("connection to db.internal failed for user admin")

    monkeypatch.setattr(status, "summary", broken)
    reply = client.get("/api/operator/office")
    assert reply.status_code == 503 and reply.json() == {"error_code": "ADMIN_STORAGE_OR_OPERATION_UNAVAILABLE"}


def test_before_the_office_tables_exist_the_summary_says_so_and_shows_no_secret(monkeypatch):
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.return_value.fetchone.return_value = (False,)
    monkeypatch.setattr(tasks, "connect", lambda: conn)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DATABASE_URL", "STOCK_MACHINE_ADMIN_TOKEN"):
        monkeypatch.setenv(name, SENTINEL)
    for name in ("OFFICE_ENABLED", "OFFICE_PAPER_ENABLED", "OFFICE_MODEL_PROVIDER", "OFFICE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    out = status.summary()
    assert out["schema"] == "NOT_MIGRATED" and out["tables_missing"] == sorted(status.TABLES)
    assert out["switches"] == {"office_enabled": False, "paper_enabled": False, "provider": "none", "model": None,
                               "daily_cap_usd": "5", "monthly_cap_usd": "100"}
    assert out["boundaries"]["broker_submission"] is False and out["boundaries"]["live_trading"] is False
    assert SENTINEL not in json.dumps(out)


def test_the_status_module_only_selects():
    source = inspect.getsource(status)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP|CREATE)\b", source)
    assert "os.environ" not in source and "getenv" not in source      # settings come through office config only
    assert "require_enabled" not in source                            # readable while the office is off


def test_the_console_section_is_read_only_and_the_http_mcp_is_unchanged():
    page, script = (ROOT / "webui/admin.html").read_text(), (ROOT / "webui/admin.js").read_text()
    assert 'id="office-status"' in page and "Read only." in page
    body = script.split("async function refreshOffice(){", 1)[1].split("\n}\n", 1)[0]
    assert "api('/office')" in body and "'POST'" not in body and "innerHTML" not in script
    assert "office" not in (ROOT / "stock_machine/mcp_server/http.py").read_text().lower()
    assert "office" not in (ROOT / "stock_machine/mcp_server/api_gateway.py").read_text().lower()
