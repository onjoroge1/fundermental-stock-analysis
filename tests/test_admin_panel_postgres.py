"""Real database security/integrity tests in a temporary, nonproduction schema."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from stock_machine.admin_panel import store,security

INITIAL="fixture-initial-password"
REPLACEMENT="fixture-private-password-at-least-fifteen"


@pytest.fixture
def pg(monkeypatch):
    dsn=os.getenv("TEST_DATABASE_URL")
    if not dsn:pytest.skip("TEST_DATABASE_URL required")
    import psycopg
    schema="test_operator_"+uuid4().hex
    with psycopg.connect(dsn,autocommit=True) as c:c.execute(f'CREATE SCHEMA "{schema}"')
    def connect():return psycopg.connect(dsn,options=f"-c search_path={schema}")
    try:
        path=Path(__file__).parents[1]/"migrations/versions/0022_admin_panel.py"
        spec=importlib.util.spec_from_file_location("operator_migration",path)
        m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        with connect() as conn:
            m.op=SimpleNamespace(execute=conn.execute);m.upgrade()
        monkeypatch.setattr(store,"connect",connect)
        monkeypatch.setenv("AGENT_LAB_ENABLED","true")
        yield connect
    finally:
        with psycopg.connect(dsn,autocommit=True) as c:c.execute(f'DROP SCHEMA "{schema}" CASCADE')


def account():
    store.bootstrap(INITIAL)
    raw,info=store.login("admin",INITIAL)
    return raw,info


def test_bootstrap_atomic_once_no_plaintext_stored(pg):
    assert store.setup_required()
    account()
    assert not store.setup_required()
    with pytest.raises(security.PanelError,match="SETUP_ALREADY"):store.bootstrap(INITIAL)
    with pg() as c:
        row=c.execute("SELECT password_hash FROM operator_users").fetchone()[0]
        assert row.startswith("$argon2id$") and INITIAL not in row
        assert INITIAL not in str(c.execute("SELECT details FROM operator_audit").fetchall())


def test_session_requires_correct_csrf_and_private_password(pg):
    raw,info=account()
    assert store.session(raw)["username"]=="admin"
    with pytest.raises(security.PanelError,match="CSRF"):store.session(raw,write=True,csrf="bad")
    with pytest.raises(security.PanelError,match="PASSWORD_CHANGE"):store.session(raw,write=True,csrf=info["csrf_token"])
    new,updated=store.change_password("admin",INITIAL,REPLACEMENT)
    with pytest.raises(security.PanelError,match="LOGIN_REQUIRED"):store.session(raw)
    assert store.session(new,write=True,csrf=updated["csrf_token"])["must_change_password"] is False
    store.logout(new,"admin")
    with pytest.raises(security.PanelError):store.session(new)


def test_expired_idle_and_disabled_sessions_fail(pg):
    raw,_=account()
    with pg() as c:c.execute("UPDATE operator_sessions SET last_seen=now()-interval '31 minutes'")
    with pytest.raises(security.PanelError):store.session(raw)
    raw,_=store.login("admin",INITIAL)
    with pg() as c:c.execute("UPDATE operator_sessions SET expires_at=now()-interval '1 second'")
    with pytest.raises(security.PanelError):store.session(raw)
    raw,_=store.login("admin",INITIAL)
    with pg() as c:c.execute("UPDATE operator_users SET disabled=true")
    with pytest.raises(security.PanelError):store.session(raw)


def test_throttling_persists_across_failed_logins(pg):
    store.bootstrap(INITIAL)
    for _ in range(10):
        with pytest.raises(security.PanelError,match="INVALID_LOGIN"):store.login("unknown",INITIAL)
    with pytest.raises(security.PanelError,match="RATE_LIMITED"):store.login("admin",INITIAL)


def test_settings_cas_audit_and_deployment_restriction(pg,monkeypatch):
    account();before=store.controls()
    after=store.set_capture_pause("admin",True,before["version"],"Stop test captures")
    assert after["capture_paused"] and not after["capture_enabled"]
    with pytest.raises(security.PanelError,match="SETTINGS_CHANGED"):store.set_capture_pause("admin",False,before["version"],"stale update")
    store.set_capture_pause("admin",False,after["version"],"Resume tests")
    monkeypatch.setenv("AGENT_LAB_ENABLED","false")
    assert not store.controls()["capture_enabled"]
    with pytest.raises(security.PanelError):store.require_capture_enabled()
    assert len([x for x in store.recent_audit() if x["event"]=="CAPTURE_PAUSE_CHANGED"])==2


def test_audit_mutations_denied(pg):
    import psycopg
    account()
    for sql in ("UPDATE operator_audit SET actor='changed'","DELETE FROM operator_audit","TRUNCATE operator_audit"):
        with pytest.raises(psycopg.Error):
            with pg() as c:c.execute(sql)


def test_run_creation_idempotent_and_budgeted(pg):
    account();rid=str(uuid4())
    assert not store.create_run("admin",rid)["replayed"]
    assert store.create_run("admin",rid)["replayed"]
    assert len(store.runs()[0]["items"])==5
    store.create_run("admin",str(uuid4()));store.create_run("admin",str(uuid4()))
    with pytest.raises(security.PanelError,match="DAILY_PILOT"):store.create_run("admin",str(uuid4()))


def test_pause_prevents_claim_and_leases_are_fenced(pg):
    account();rid=str(uuid4());store.create_run("admin",rid)
    first=store.claim(rid);assert first
    second=store.claim(rid);assert second["ticker"]!=first["ticker"]
    with pg() as c:c.execute("UPDATE operator_pilot_items SET lease_token=%s WHERE run_id=%s AND ticker=%s",(str(uuid4()),rid,first["ticker"]))
    with pytest.raises(security.PanelError,match="LEASE_CHANGED"):store.finish(first,{"fixture":True})
    store.set_capture_pause("admin",True,1,"Pause pending pilot")
    with pytest.raises(security.PanelError,match="PAUSED"):store.claim(rid)


def test_only_session_hash_persisted(pg):
    raw,_=account()
    with pg() as c:
        rows=c.execute("SELECT token_hash FROM operator_sessions").fetchall()
    assert rows==[(security.token_hash(raw),)]
