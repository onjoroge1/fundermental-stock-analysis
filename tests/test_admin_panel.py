"""Owner-panel security boundaries. Synthetic passwords never seed runtime data."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from stock_machine.admin_panel import api, operations, security, store

ORIGIN = "https://testserver"
H = {"Origin": ORIGIN, "X-CSRF-Token": "fixture-csrf"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app, base_url=ORIGIN)


def test_passwords_are_salted_argon2id_not_plaintext():
    value = "fixture-only-password-not-a-real-account"
    a, b = security.hash_password(value), security.hash_password(value)
    assert a.startswith("$argon2id$") and a != b and value not in a
    assert security.verify_password(a, value)
    assert not security.verify_password(a, "incorrect-fixture-password")
    assert not security.verify_password("broken", value)


@pytest.mark.parametrize("value", ["true", "short", "x" * 129, "x" * 20 + "\n"])
def test_bad_passwords_are_rejected(value):
    with pytest.raises(security.PanelError):
        security.validate_password(value)


def test_random_session_is_stored_as_hash():
    a, b = security.token(), security.token()
    assert len(a) >= 40 and a != b
    assert len(security.token_hash(a)) == 64 and security.token_hash(a) != a


def test_setup_checks_owner_auth_before_database(client, monkeypatch):
    from stock_machine import automation_api
    monkeypatch.setattr(automation_api, "_require_admin", lambda _: (_ for _ in ()).throw(HTTPException(401)))
    monkeypatch.setattr(store, "bootstrap", lambda *_: pytest.fail("Unauthorized bootstrap touched DB"))
    r = client.post("/api/operator/setup", headers=H, json={"username":"admin", "password":"fixture-password-123"})
    assert r.status_code == 401 and r.json()["error_code"] == "OWNER_AUTHENTICATION_REQUIRED"
    assert "fixture-password" not in r.text


def test_setup_uses_username_admin_without_inventing_default_password(client, monkeypatch):
    from stock_machine import automation_api
    monkeypatch.setattr(automation_api, "_require_admin", lambda _: None)
    called = []
    monkeypatch.setattr(store, "bootstrap", lambda p: called.append(p) or {"username":"admin"})
    r = client.post("/api/operator/setup", headers={**H,"Authorization":"Bearer test-only-owner-proof"}, json={"username":"admin","password":"fixture-password-123"})
    assert r.status_code == 201 and called == ["fixture-password-123"]


@pytest.mark.parametrize("origin", [None,"https://evil.invalid","https://testserver.evil.invalid","http://testserver"])
def test_login_rejects_wrong_or_missing_origin(client,monkeypatch,origin):
    monkeypatch.setattr(store,"login",lambda *_: pytest.fail("Cross-site login reached password verification"))
    r=client.post("/api/operator/login",headers={"Origin":origin} if origin else {},json={"username":"admin","password":"fixture-password"})
    assert r.status_code==403


def test_login_sets_secure_host_cookie_and_no_token_in_body(client,monkeypatch):
    monkeypatch.setattr(store,"login",lambda *_:("opaque-session-fixture",{"username":"admin","csrf_token":"fixture-csrf","must_change_password":True}))
    r=client.post("/api/operator/login",headers=H,json={"username":"admin","password":"fixture-password"})
    assert r.status_code==200
    cookie=r.headers["set-cookie"]
    for flag in ("__Host-stock_admin=", "Secure", "HttpOnly", "SameSite=strict", "Path=/"):
        assert flag in cookie
    assert "Domain=" not in cookie and "opaque-session-fixture" not in r.text
    assert r.headers["cache-control"]=="no-store" and "frame-ancestors 'none'" in r.headers["content-security-policy"]


@pytest.mark.parametrize("payload", [{"password":"DO_NOT_ECHO"},{"username":"admin","password":"DO_NOT_ECHO","mode":"LIVE"},[]])
def test_invalid_credentials_not_echoed_in_validation_errors(client,payload):
    r=client.post("/api/operator/login",headers=H,json=payload)
    assert r.status_code==400 and "DO_NOT_ECHO" not in r.text


def test_body_size_and_content_type_bounds(client):
    assert client.post("/api/operator/login",headers=H,content="x"*9000).status_code==415
    assert client.post("/api/operator/login",headers={**H,"Content-Type":"application/json"},content='"'+'x'*9000+'"').status_code==413


def test_unknown_storage_error_redacted(client,monkeypatch):
    monkeypatch.setattr(store,"setup_required",lambda:(_ for _ in ()).throw(RuntimeError("password=NEVER_DISPLAY")))
    r=client.get("/api/operator/session")
    assert r.status_code==503 and "NEVER_DISPLAY" not in r.text


def test_session_csrf_required_for_settings(client,monkeypatch):
    def gate(raw,**kw):
        assert kw["write"] and kw["csrf"] is None
        raise security.PanelError("CSRF_REJECTED",403)
    monkeypatch.setattr(store,"session",gate)
    monkeypatch.setattr(store,"set_capture_pause",lambda *_:pytest.fail("CSRF bypass"))
    r=client.post("/api/operator/controls",headers={"Origin":ORIGIN},json={"capture_paused":False,"expected_version":1,"reason":"test"})
    assert r.status_code==403


def test_temporary_password_cannot_operate_panel(client,monkeypatch):
    monkeypatch.setattr(store,"session",lambda *a,**kw:{"username":"admin","must_change_password":True,"csrf_token":"fixture-csrf"})
    monkeypatch.setattr(store,"create_run",lambda *_:pytest.fail("Temporary password allowed operations"))
    r=client.post("/api/operator/runs",headers=H,json={"request_id":"00000000-0000-4000-8000-000000000001"})
    assert r.status_code==403


def test_unauthenticated_dashboard_and_operations_blocked(client,monkeypatch):
    monkeypatch.setattr(store,"session",lambda *a,**kw:(_ for _ in ()).throw(security.PanelError("LOGIN_REQUIRED",401)))
    assert client.get("/api/operator/dashboard").status_code==401
    assert client.post("/api/operator/runs",headers=H,json={}).status_code==401


def test_control_rejects_unknown_fields_and_string_boolean(client,monkeypatch):
    monkeypatch.setattr(store,"session",lambda *a,**kw:{"username":"admin","must_change_password":False})
    for body in ({"capture_paused":"false","expected_version":1,"reason":"test"},
                 {"capture_paused":False,"expected_version":True,"reason":"test"},
                 {"capture_paused":False,"expected_version":1,"reason":"test","trading":True}):
        assert client.post("/api/operator/controls",headers=H,json=body).status_code==400


def test_preview_cannot_bootstrap_with_production_database(client,monkeypatch):
    monkeypatch.setenv("VERCEL","1");monkeypatch.setenv("VERCEL_ENV","preview")
    r=client.post("/api/operator/setup",headers=H,json={"username":"admin","password":"fixture-password-123"})
    assert r.status_code==403


def test_no_broker_or_arbitrary_job_routes(client):
    for suffix in ("orders","execute","sql","migrate","rewards","promote","jobs"):
        assert client.post("/api/operator/"+suffix,headers=H,json={}).status_code==404


def test_ui_has_no_browser_credential_storage_or_html_injection():
    root=Path(__file__).parents[1]
    script=(root/"webui/admin.js").read_text()
    assert "textContent" in script and "innerHTML" not in script
    for bad in ("localStorage","sessionStorage","document.cookie"):
        assert bad not in script
    assert "Bearer " in script
    assert 'value="admin"' in (root/"webui/admin.html").read_text()


def test_paused_control_applies_to_legacy_bearer_capture(monkeypatch):
    from stock_machine.agents import api as agents, journal
    app=FastAPI();app.include_router(agents.router)
    monkeypatch.setattr(agents,"configured_admin_token",lambda:"x"*32)
    monkeypatch.setattr(agents,"panel_capture_paused",lambda:True)
    monkeypatch.setenv("AGENT_LAB_ENABLED","true")
    monkeypatch.setattr(journal,"capture",lambda *a,**kw:pytest.fail("Paused legacy request reached capture"))
    r=TestClient(app).post("/api/admin/agents/AAPL/capture",headers={"Authorization":"Bearer "+"x"*32},json={"idempotency_key":"test-paused-request"})
    assert r.status_code==409


def test_panel_page_and_assets_have_no_database_dependency(monkeypatch):
    from stock_machine.webapp_automation import app
    from stock_machine import db
    monkeypatch.setattr(db,"connect",lambda:pytest.fail("Static panel required a database"))
    with TestClient(app) as c:
        assert c.get("/admin").status_code==200
        assert c.get("/ui/admin.css").status_code==200
        assert c.get("/ui/admin.js").status_code==200
        assert 'href="/admin"' in c.get("/").text
