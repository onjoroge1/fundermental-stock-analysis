"""Database-backed authentication and controls. No schema writes on HTTP reads."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from .security import (DUMMY_HASH, HASHER, IDLE_SECONDS, SESSION_SECONDS, PanelError,
                       hash_password, same, token, token_hash, validate_password, verify_password)


def connect():
    from ..db import connect as connection
    return connection()


def audit(conn, actor: str, event: str, details: dict):
    from psycopg.types.json import Jsonb
    # Callers supply only fixed event fields; never request bodies or exceptions.
    conn.execute("INSERT INTO operator_audit(actor,event,details) VALUES (%s,%s,%s)",
                 (actor, event, Jsonb(details)))


def setup_required() -> bool:
    with connect() as conn:
        return conn.execute("SELECT NOT EXISTS(SELECT 1 FROM operator_users)").fetchone()[0]


def bootstrap(password: str):
    """API caller MUST prove the existing owner admin credential first."""
    validate_password(password, initial=True)
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('operator-bootstrap',0))")
        if conn.execute("SELECT 1 FROM operator_users LIMIT 1").fetchone():
            raise PanelError("SETUP_ALREADY_COMPLETED", 409)
        encoded = hash_password(password, initial=True)
        conn.execute("INSERT INTO operator_users(username,password_hash) VALUES ('admin',%s)", (encoded,))
        audit(conn, "owner-bootstrap", "OWNER_CREATED", {"username": "admin", "password_change_required": True})
    return {"status": "CREATED", "username": "admin", "password_change_required": True}


def _new_session(conn, username):
    raw, csrf = token(), token()
    expires = datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)
    conn.execute("DELETE FROM operator_sessions WHERE expires_at <= now() OR last_seen < now() - interval '30 minutes'")
    # A single owner session also makes the UI's logout semantics unambiguous.
    conn.execute("DELETE FROM operator_sessions WHERE username=%s", (username,))
    conn.execute("INSERT INTO operator_sessions(token_hash,username,csrf_token,expires_at) VALUES (%s,%s,%s,%s)",
                 (token_hash(raw), username, csrf, expires))
    return raw, csrf


def login(username: str, password: str):
    # Persistent global throttle: one owner, bounded rows, applies across workers.
    with connect() as conn:
        count = conn.execute("""UPDATE operator_auth_limits SET
            attempts=CASE WHEN window_start < now()-interval '5 minutes' THEN 1 ELSE attempts+1 END,
            window_start=CASE WHEN window_start < now()-interval '5 minutes' THEN now() ELSE window_start END
            WHERE singleton RETURNING attempts""").fetchone()
        if count is None:
            raise PanelError("AUTH_STORAGE_UNAVAILABLE", 503)
    if count[0] > 10:
        raise PanelError("LOGIN_RATE_LIMITED", 429)
    with connect() as conn:
        # Serialize expensive password checks across instances; no memory storm.
        if not conn.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('operator-login',0))").fetchone()[0]:
            raise PanelError("LOGIN_BUSY", 429)
        row = conn.execute("SELECT username,password_hash,must_change_password,disabled FROM operator_users WHERE username=%s FOR UPDATE", (username,)).fetchone()
        correct = verify_password(row[1] if row else DUMMY_HASH, password)
        accepted = bool(row and correct and not row[3])
        if accepted:
            if HASHER.check_needs_rehash(row[1]):
                conn.execute("UPDATE operator_users SET password_hash=%s WHERE username=%s", (HASHER.hash(password), username))
            raw, csrf = _new_session(conn, username)
            audit(conn, username, "LOGIN_SUCCEEDED", {})
        else:
            audit(conn, "unauthenticated", "LOGIN_REJECTED", {})
    if not accepted:
        raise PanelError("INVALID_LOGIN", 401)
    return raw, {"username": row[0], "must_change_password": row[2], "csrf_token": csrf}


def session(raw: str, *, csrf: str | None = None, write: bool = False, allow_password_change: bool = False):
    if not raw or len(raw) > 128:
        raise PanelError("LOGIN_REQUIRED", 401)
    with connect() as conn:
        row = conn.execute("""SELECT s.username,s.csrf_token,u.must_change_password FROM operator_sessions s
            JOIN operator_users u ON u.username=s.username
            WHERE s.token_hash=%s AND s.expires_at > now() AND s.last_seen > now()-interval '30 minutes'
            AND NOT u.disabled""", (token_hash(raw),)).fetchone()
        if row is None:
            raise PanelError("LOGIN_REQUIRED", 401)
        if write and (not csrf or not same(row[1], csrf)):
            raise PanelError("CSRF_REJECTED", 403)
        if write and row[2] and not allow_password_change:
            raise PanelError("PASSWORD_CHANGE_REQUIRED", 403)
        conn.execute("UPDATE operator_sessions SET last_seen=now() WHERE token_hash=%s", (token_hash(raw),))
    return {"username": row[0], "csrf_token": row[1], "must_change_password": row[2]}


def logout(raw: str, actor: str):
    with connect() as conn:
        conn.execute("DELETE FROM operator_sessions WHERE token_hash=%s", (token_hash(raw),))
        audit(conn, actor, "LOGOUT", {})


def change_password(actor: str, current: str, replacement: str):
    validate_password(replacement)
    if current == replacement:
        raise PanelError("NEW_PASSWORD_MUST_DIFFER")
    with connect() as conn:
        row = conn.execute("SELECT password_hash FROM operator_users WHERE username=%s FOR UPDATE", (actor,)).fetchone()
        if not row or not verify_password(row[0], current):
            raise PanelError("INVALID_LOGIN", 401)
        conn.execute("UPDATE operator_users SET password_hash=%s,must_change_password=false WHERE username=%s", (hash_password(replacement), actor))
        raw, csrf = _new_session(conn, actor)
        audit(conn, actor, "PASSWORD_CHANGED", {})
    return raw, {"username": actor, "must_change_password": False, "csrf_token": csrf}


def controls(conn=None):
    if conn is None:
        with connect() as owned:
            return controls(owned)
    row = conn.execute("SELECT capture_paused,version,updated_at::text FROM operator_controls WHERE singleton").fetchone()
    if row is None:
        raise PanelError("CONTROLS_UNAVAILABLE", 503)
    allowed = os.getenv("AGENT_LAB_ENABLED", "false").lower() == "true"
    return {"capture_paused": row[0], "version": row[1], "updated_at": row[2],
            "deployment_permits_capture": allowed, "capture_enabled": allowed and not row[0],
            "scope": "Journal captures and admin pilot runs only; existing data/news schedules are unchanged."}


def require_capture_enabled():
    state = controls()
    if not state["capture_enabled"]:
        raise PanelError("CAPTURE_PAUSED" if state["capture_paused"] else "DEPLOYMENT_CAPTURE_DISABLED", 409)


def set_capture_pause(actor: str, paused: bool, expected_version: int, reason: str):
    with connect() as conn:
        before = conn.execute("SELECT capture_paused,version FROM operator_controls WHERE singleton FOR UPDATE").fetchone()
        if not before or before[1] != expected_version:
            raise PanelError("SETTINGS_CHANGED_RELOAD", 409)
        conn.execute("UPDATE operator_controls SET capture_paused=%s,version=version+1,updated_at=now() WHERE singleton", (paused,))
        audit(conn, actor, "CAPTURE_PAUSE_CHANGED", {"before": before[0], "after": paused, "reason": reason[:300], "version": before[1]+1})
        return controls(conn)


def recent_audit():
    with connect() as conn:
        rows = conn.execute("SELECT actor,event,details,occurred_at::text FROM operator_audit ORDER BY id DESC LIMIT 30").fetchall()
    return [dict(zip(("actor", "event", "details", "occurred_at"), r)) for r in rows]


def create_run(actor: str, run_id: str):
    from ..agents.contracts import PILOT
    run_id = str(UUID(run_id))
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('operator-pilot-create',0))")
        if conn.execute("SELECT 1 FROM operator_pilot_runs WHERE run_id=%s AND actor=%s", (run_id, actor)).fetchone():
            return {"run_id": run_id, "replayed": True}
        if not controls(conn)["capture_enabled"]:
            raise PanelError("CAPTURE_PAUSED", 409)
        count = conn.execute("SELECT count(*) FROM operator_pilot_runs WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'").fetchone()[0]
        if count >= 3:
            raise PanelError("DAILY_PILOT_LIMIT_REACHED", 429)
        conn.execute("INSERT INTO operator_pilot_runs(run_id,actor) VALUES (%s,%s)", (run_id, actor))
        for ticker in PILOT:
            conn.execute("INSERT INTO operator_pilot_items(run_id,ticker) VALUES (%s,%s)", (run_id, ticker))
        audit(conn, actor, "PILOT_REQUESTED", {"run_id": run_id, "tickers": list(PILOT)})
    return {"run_id": run_id, "replayed": False}


def runs():
    with connect() as conn:
        rows = conn.execute("""SELECT r.run_id::text,r.created_at::text,i.ticker,i.status,i.attempts,i.result
            FROM (SELECT * FROM operator_pilot_runs ORDER BY created_at DESC LIMIT 10) r
            JOIN operator_pilot_items i ON r.run_id=i.run_id ORDER BY r.created_at DESC,i.ticker""").fetchall()
    out = {}
    for run_id, created, ticker, status, attempts, result in rows:
        item = out.setdefault(run_id, {"run_id": run_id, "created_at": created, "items": []})
        item["items"].append({"ticker": ticker, "status": status, "attempts": attempts, "result": result})
    return list(out.values())


def claim(run_id: str):
    with connect() as conn:
        if not controls(conn)["capture_enabled"]:
            raise PanelError("CAPTURE_PAUSED", 409)
        conn.execute("""UPDATE operator_pilot_items SET status='FAILED',result='{"error_code":"LEASE_EXHAUSTED"}'::jsonb,
            lease_until=NULL,lease_token=NULL WHERE run_id=%s AND status='RUNNING'
            AND lease_until < now() AND attempts>=3""", (run_id,))
        row = conn.execute("""SELECT ticker,attempts FROM operator_pilot_items WHERE run_id=%s
            AND (status='PENDING' OR (status='RUNNING' AND lease_until < now())) AND attempts < 3
            ORDER BY ticker FOR UPDATE SKIP LOCKED LIMIT 1""", (run_id,)).fetchone()
        if row is None:
            return None
        lease = str(uuid4())
        conn.execute("""UPDATE operator_pilot_items SET status='RUNNING',attempts=attempts+1,
            lease_token=%s,lease_until=now()+interval '10 minutes',updated_at=now()
            WHERE run_id=%s AND ticker=%s""", (lease, run_id, row[0]))
    return {"run_id": run_id, "ticker": row[0], "lease_token": lease, "attempt": row[1]+1}


def finish(item: dict, result: dict, *, failed=False):
    from psycopg.types.json import Jsonb
    with connect() as conn:
        updated = conn.execute("""UPDATE operator_pilot_items SET status=%s,result=%s,
            lease_token=NULL,lease_until=NULL,updated_at=now()
            WHERE run_id=%s AND ticker=%s AND lease_token=%s RETURNING run_id""",
            ("FAILED" if failed else "COMPLETED", Jsonb(result), item["run_id"], item["ticker"], item["lease_token"])).fetchone()
        if not updated:
            raise PanelError("TASK_LEASE_CHANGED", 409)
        audit(conn, "admin", "PILOT_ITEM_FINISHED", {"run_id": item["run_id"], "ticker": item["ticker"], "failed": failed})
    return {"ticker": item["ticker"], "status": "FAILED" if failed else "COMPLETED", "result": result}
