"""Owner-panel security boundaries for the simplified operations model."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from stock_machine.admin_panel import api, security, store

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


def test_random_session_is_stored_as_hash():
    a, b = security.token(), security.token()
    assert len(a) >= 40 and a != b
    assert len(security.token_hash(a)) == 64 and security.token_hash(a) != a


@pytest.mark.parametrize("origin", [None,"https://evil.invalid","https://testserver.evil.invalid","http://testserver"])
def test_login_rejects_wrong_or_missing_origin(client, monkeypatch, origin):
    monkeypatch.setattr(store, "login", lambda *_: pytest.fail("Cross-site login reached password verification"))
    headers = {"Origin": origin} if origin else {}
    r = client.post("/api/operator/login", headers=headers, json={"username":"admin","password":"fixture-password"})
    assert r.status_code == 403


def test_login_sets_secure_host_cookie_and_no_token_in_body(client, monkeypatch):
    monkeypatch.setattr(store, "login", lambda *_:("opaque-session-fixture",{"username":"admin","csrf_token":"fixture-csrf","must_change_password":False}))
    r = client.post("/api/operator/login", headers=H, json={"username":"admin","password":"fixture-password"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    for flag in ("__Host-stock_admin=", "Secure", "HttpOnly", "SameSite=strict", "Path=/"):
        assert flag in cookie
    assert "Domain=" not in cookie and "opaque-session-fixture" not in r.text
    assert r.headers["cache-control"] == "no-store"


def test_session_without_cookie_shows_plain_login(client, monkeypatch):
    monkeypatch.setattr(store, "session", lambda *a, **kw: (_ for _ in ()).throw(security.PanelError("LOGIN_REQUIRED", 401)))
    r = client.get("/api/operator/session")
    assert r.status_code == 200 and r.json() == {"authenticated": False}


def test_no_setup_or_token_bootstrap_route(client):
    r = client.post("/api/operator/setup", headers=H, json={"username":"admin","password":"fixture-password"})
    assert r.status_code == 404


def test_invalid_credentials_not_echoed(client):
    r = client.post("/api/operator/login", headers=H, json={"username":"admin","password":"DO_NOT_ECHO","mode":"LIVE"})
    assert r.status_code == 400 and "DO_NOT_ECHO" not in r.text


def test_body_size_and_content_type_bounds(client):
    assert client.post("/api/operator/login",headers=H,content="x"*9000).status_code==415
    assert client.post("/api/operator/login",headers={**H,"Content-Type":"application/json"},content='"'+'x'*9000+'"').status_code==413


def test_session_csrf_required_for_settings(client, monkeypatch):
    def gate(raw, **kw):
        assert kw["write"] and kw["csrf"] is None
        raise security.PanelError("CSRF_REJECTED",403)
    monkeypatch.setattr(store,"session",gate)
    monkeypatch.setattr(store,"set_capture_pause",lambda *_:pytest.fail("CSRF bypass"))
    r=client.post("/api/operator/controls",headers={"Origin":ORIGIN},json={"capture_paused":False,"expected_version":1,"reason":"test"})
    assert r.status_code==403


def test_unauthenticated_dashboard_and_operations_blocked(client, monkeypatch):
    monkeypatch.setattr(store,"session",lambda *a,**kw:(_ for _ in ()).throw(security.PanelError("LOGIN_REQUIRED",401)))
    assert client.get("/api/operator/dashboard").status_code==401
    assert client.post("/api/operator/runs",headers=H,json={}).status_code==401


def test_control_rejects_unknown_fields_and_string_boolean(client, monkeypatch):
    monkeypatch.setattr(store,"session",lambda *a,**kw:{"username":"admin","must_change_password":False})
    for body in ({"capture_paused":"false","expected_version":1,"reason":"test"},
                 {"capture_paused":False,"expected_version":True,"reason":"test"},
                 {"capture_paused":False,"expected_version":1,"reason":"test","trading":True}):
        assert client.post("/api/operator/controls",headers=H,json=body).status_code==400


def test_no_broker_or_arbitrary_job_routes(client):
    for suffix in ("orders","execute","sql","migrate","rewards","promote","jobs"):
        assert client.post("/api/operator/"+suffix,headers=H,json={}).status_code==404


def test_ui_has_no_browser_credential_storage_token_prompt_or_html_injection():
    root=Path(__file__).parents[1]
    script=(root/"webui/admin.js").read_text()
    html=(root/"webui/admin.html").read_text()
    assert "textContent" in script and "innerHTML" not in script
    for bad in ("localStorage","sessionStorage","document.cookie","Bearer ","owner-token","setup-form"):
        assert bad not in script and bad not in html
    assert 'value="admin"' in html


def test_paused_control_applies_to_legacy_bearer_capture(monkeypatch):
    from stock_machine.agents import api as agents, journal
    app=FastAPI();app.include_router(agents.router)
    monkeypatch.setattr(agents,"configured_admin_token",lambda:"x"*32)
    monkeypatch.setattr(agents,"panel_capture_paused",lambda:True)
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


def test_intelligence_summary_is_read_only_owner_projection(monkeypatch):
    from stock_machine.admin_panel import operations
    from stock_machine import research_store

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): return False

    monkeypatch.setattr(store, "connect", lambda: Conn())
    def latest(conn, kind, ticker=None):
        if ticker != "AAPL":
            return None
        if kind == "AGENT_INTELLIGENCE_V2":
            return {"payload": {
                "mode":"SHADOW","decision_id":"d1",
                "state":{"as_of":"2026-09-18","direction":"BULLISH","bias_score":.42,
                         "paper_eligible":True,"blockers":[],
                         "technical":{"classification":{"trend":"UP","volatility_regime":"NORMAL"}},
                         "news":{"features":{"signed_event_pressure":.15,"event_counts":{"GUIDANCE_RAISE":1}}},
                         "option_surface":{"features":{"atm_iv":.3}}},
                "router":{"selected":{"action":"LONG_STOCK","instrument":"STOCK"},
                          "candidates":[{"action":"NO_TRADE","eligible":True,"blockers":[]},
                                        {"action":"LONG_STOCK","eligible":True,"blockers":[]}]},
                "bandit":{"alpha":.35,"selected":{"action":"LONG_STOCK","mean":.5,
                           "uncertainty":.6,"ucb":.71,"observations":2}},
                "selected":{"action":"LONG_STOCK","instrument":"STOCK"},
            }}
        if kind == "AGENT_REWARD_V3":
            return {"payload":{"reward":{"reward":1.25}}}
        return None
    monkeypatch.setattr(research_store, "latest", latest)
    monkeypatch.setattr(
        research_store, "get",
        lambda conn, kind, key: ({"payload":{"reward":{"reward":1.25}}}
                                if kind == "AGENT_REWARD_V3" and key == "d1" else None),
    )
    value=operations.intelligence_summary()
    aapl=next(r for r in value["rows"] if r["ticker"]=="AAPL")
    assert aapl["ticker"]=="AAPL"
    assert aapl["direction"]=="BULLISH"
    assert aapl["bandit_selected"]=="LONG_STOCK"
    assert aapl["bandit_exploration_bonus"]==pytest.approx(.21)
    assert aapl["eligible_actions"]==["NO_TRADE","LONG_STOCK"]
    assert aapl["latest_reward"]==1.25
    assert aapl["reward_status"]=="SCORED_REALIZED_PAPER"
    assert value["broker_submission"] is False


def test_intelligence_summary_surfaces_latest_v2_failure(monkeypatch):
    from stock_machine.admin_panel import operations
    from stock_machine import research_store

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): return False

    monkeypatch.setattr(store, "connect", lambda: Conn())

    def latest(conn, kind, ticker=None):
        if ticker != "AAPL":
            return None
        if kind == "AGENT_INTELLIGENCE_V2":
            return {"recorded_at": "2026-09-27T19:00:00+00:00", "payload": {"status": "OK"}}
        if kind == "AGENT_INTELLIGENCE_V2_FAILURE":
            return {"recorded_at": "2026-09-27T20:00:00+00:00", "payload": {
                "status": "UNAVAILABLE", "mode": "SHADOW", "decision_id": "d2",
                "reason_code": "INTELLIGENCE_PRICE_DATE_MISMATCH",
            }}
        return None

    monkeypatch.setattr(research_store, "latest", latest)
    monkeypatch.setattr(research_store, "get", lambda *args, **kwargs: None)
    value = operations.intelligence_summary()
    aapl = next(r for r in value["rows"] if r["ticker"]=="AAPL")
    assert aapl == {
        "ticker": "AAPL", "status": "UNAVAILABLE", "mode": "SHADOW",
        "decision_id": "d2", "reason_code": "INTELLIGENCE_PRICE_DATE_MISMATCH",
        "recorded_at": "2026-09-27T20:00:00+00:00",
    }
    assert [row["status"] for row in value["rows"] if row["ticker"]!="AAPL"] == ["NOT_RUN"] * 53


@pytest.mark.parametrize(
    ("intelligence", "expected_status", "expected_choice"),
    [
        (
            {
                "status": "OK", "mode": "SHADOW", "decision_id": "run-success",
                "state": {
                    "as_of": "2026-09-25", "direction": "BULLISH", "bias_score": 0.31,
                    "technical": {"classification": {"trend": "UP", "volatility_regime": "NORMAL"}},
                    "news": {"features": {"signed_event_pressure": 0.1, "event_counts": {}}},
                },
                "router": {"selected": {"action": "LONG_STOCK", "instrument": "STOCK"}},
                "bandit": {"selected": {"action": "LONG_STOCK", "ucb": 0.5, "observations": 0}},
                "selected": {"action": "LONG_STOCK", "instrument": "STOCK"},
            },
            "OK",
            "LONG_STOCK",
        ),
        (
            {
                "status": "UNAVAILABLE", "mode": "SHADOW", "decision_id": "run-failure",
                "reason_code": "INTELLIGENCE_PRICE_DATE_MISMATCH",
            },
            "UNAVAILABLE",
            None,
        ),
    ],
)
def test_intelligence_summary_recovers_completed_pilot_result(
        monkeypatch, intelligence, expected_status, expected_choice):
    from stock_machine.admin_panel import operations
    from stock_machine import research_store

    class Result:
        def fetchall(self):
            return [(
                "AAPL",
                {"intelligence_v2": intelligence},
                "2026-09-28T11:30:24+00:00",
            )]

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, *args): return Result()

    monkeypatch.setattr(store, "connect", lambda: Conn())
    monkeypatch.setattr(research_store, "latest", lambda *args, **kwargs: None)
    monkeypatch.setattr(research_store, "get", lambda *args, **kwargs: None)

    value = operations.intelligence_summary()
    aapl = next(r for r in value["rows"] if r["ticker"]=="AAPL")
    assert aapl["status"] == expected_status
    assert aapl.get("bandit_selected") == expected_choice
    assert aapl["decision_id"] == intelligence["decision_id"]
    assert aapl["recorded_at"] == "2026-09-28T11:30:24+00:00"
    assert [row["status"] for row in value["rows"] if row["ticker"]!="AAPL"] == ["NOT_RUN"] * 53
