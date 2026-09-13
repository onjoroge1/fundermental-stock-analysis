"""Append-only PostgreSQL research journal; no order/account endpoints.

Capture and its app-report outbox entry commit atomically. Per-request database
locks serialize duplicate delivery before the potentially expensive reader.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from .contracts import CaptureRequest, Decision, PILOT, POLICY_ID, Policy, ReviewRequest, canonical, digest, utc_now
from .research import build_decision, failure_decision


class JournalConflict(ValueError):
    pass


def connection():
    from .. import db
    return db.connect()


def read_research(ticker: str) -> dict:
    from ..api_v1 import stock_research
    return stock_research(ticker, include_live_quote=False)


def expected_session() -> str:
    from ..market_calendar import latest_completed_session
    return str(latest_completed_session())


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _policy(cur):
    policy = Policy().model_dump(mode="json")
    cur.execute("""INSERT INTO agent_lab_policies (policy_id, content_hash, payload)
        VALUES (%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
        (POLICY_ID, digest(policy), canonical(policy)))
    existing = _one(cur, "SELECT content_hash FROM agent_lab_policies WHERE policy_id=%s", (POLICY_ID,))
    if existing != digest(policy):
        raise JournalConflict("Policy version already exists with different contents")


def _event(cur, decision_id, event_type, key, message):
    event = {"event_type": event_type, "message": message}
    cur.execute("""INSERT INTO agent_lab_events
        (event_id, decision_id, event_type, request_key, payload)
        VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT (decision_id, request_key) DO NOTHING
        RETURNING event_id""", (str(uuid4()), decision_id, event_type, key, canonical(event)))
    inserted = cur.fetchone()
    if inserted:
        event_id = str(inserted[0])
        cur.execute("""INSERT INTO agent_lab_report_outbox (event_id, payload)
            VALUES (%s,%s::jsonb)""", (event_id, canonical({"decision_id": decision_id, **event})))
    else:
        existing = _one(cur, "SELECT payload FROM agent_lab_events WHERE decision_id=%s AND request_key=%s", (decision_id, key))
        if existing != event:
            raise JournalConflict("Idempotency key already has different review contents")


def capture(ticker: str, request: CaptureRequest, *, reader=None) -> dict:
    ticker = ticker.strip().upper()
    if ticker not in PILOT:
        raise ValueError("Ticker is outside the frozen research pilot")
    reader = reader or read_research
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"{POLICY_ID}:{ticker}:{request.idempotency_key}",))
            prior = _one(cur, """SELECT payload FROM agent_lab_decisions
                WHERE policy_id=%s AND ticker=%s AND request_key=%s""",
                (POLICY_ID, ticker, request.idempotency_key))
            if prior is not None:
                return {"replayed": True, "decision": prior}
            _policy(cur)
            previous_id = _one(cur, """SELECT decision_id::text FROM agent_lab_decisions
                WHERE policy_id=%s AND ticker=%s ORDER BY recorded_at DESC, decision_id DESC LIMIT 1""", (POLICY_ID, ticker))
            try:
                session = expected_session()
                packet = reader(ticker)
                observed = utc_now()
                decision = build_decision(ticker, packet, observed, session, previous_id)
            except Exception:
                # Failure itself is evidence. Provider exception text is deliberately
                # not stored or returned; do not expose a DSN, token or raw response.
                packet, decision = failure_decision(ticker, utc_now(), previous_id)
            payload = decision.model_dump(mode="json")
            cur.execute("""INSERT INTO agent_lab_evidence (input_sha256, payload)
                VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING""", (decision.input_sha256, canonical(packet)))
            cur.execute("""INSERT INTO agent_lab_decisions
                (decision_id,policy_id,ticker,request_key,observed_at,decided_at,status,action,input_sha256,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (decision.decision_id, POLICY_ID, ticker, request.idempotency_key,
                 decision.observed_at, decision.decided_at, decision.status, decision.action,
                 decision.input_sha256, canonical(payload)))
            _event(cur, decision.decision_id, "RECORDED", "initial", "Original research rationale frozen; execution disabled.")
    return {"replayed": False, "decision": payload}


def append_review(decision_id: str, request: ReviewRequest) -> dict:
    with connection() as conn:
        with conn.cursor() as cur:
            if _one(cur, "SELECT decision_id FROM agent_lab_decisions WHERE decision_id=%s", (decision_id,)) is None:
                raise KeyError("Decision not found")
            _event(cur, decision_id, "REVIEW", "review:" + request.idempotency_key, request.message)
    return {"status": "RECORDED", "decision_id": decision_id, "original_rationale_changed": False}


def detail(decision_id: str) -> dict | None:
    with connection() as conn:
        with conn.cursor() as cur:
            payload = _one(cur, "SELECT payload FROM agent_lab_decisions WHERE decision_id=%s", (decision_id,))
            if payload is None:
                return None
            evidence = _one(cur, "SELECT payload FROM agent_lab_evidence WHERE input_sha256=%s", (payload["input_sha256"],))
            cur.execute("""SELECT event_id::text, recorded_at::text, event_type, payload
                FROM agent_lab_events WHERE decision_id=%s ORDER BY sequence""", (decision_id,))
            events = [dict(zip(("event_id", "recorded_at", "event_type", "payload"), r)) for r in cur.fetchall()]
    return {"decision": payload, "frozen_evidence": evidence, "events": events}


def dashboard(ticker: str | None = None, status: str | None = None, limit: int = 50,
              day: date | None = None) -> dict:
    if ticker is not None and ticker not in PILOT:
        raise ValueError("Ticker is outside the frozen research pilot")
    if status is not None and status not in {"RECORDED", "BLOCKED", "FAILED"}:
        raise ValueError("Invalid status")
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be 1-100")
    clauses, params = ["policy_id=%s"], [POLICY_ID]
    if ticker:
        clauses.append("ticker=%s")
        params.append(ticker)
    if status:
        clauses.append("status=%s")
        params.append(status)
    if day:
        start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        clauses.append("recorded_at >= %s AND recorded_at < %s")
        params.extend([start, start + timedelta(days=1)])
    where = " AND ".join(clauses)  # clauses are constant SQL; inputs are bound
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT status,count(*) FROM agent_lab_decisions WHERE {where} GROUP BY status", params)
            counts = {s: n for s, n in cur.fetchall()}
            cur.execute(f"SELECT payload FROM agent_lab_decisions WHERE {where} ORDER BY recorded_at DESC,decision_id DESC LIMIT %s", [*params, limit])
            decisions = [r[0] for r in cur.fetchall()]
            cur.execute("""SELECT DISTINCT ON (ticker) ticker,payload FROM agent_lab_decisions
                WHERE policy_id=%s ORDER BY ticker,recorded_at DESC,decision_id DESC""", (POLICY_ID,))
            latest = dict(cur.fetchall())
    return {
        "status": "OK", "as_of": utc_now().isoformat(), "policy": Policy().model_dump(mode="json"),
        "scope": {"ticker": ticker, "status": status, "day_utc": day.isoformat() if day else None},
        "counts": {"total": sum(counts.values()), "recorded": counts.get("RECORDED", 0),
                   "blocked": counts.get("BLOCKED", 0), "failed": counts.get("FAILED", 0)},
        "agents": [{"ticker": t, "latest": latest.get(t), "status": latest[t]["status"] if t in latest else "NOT_STARTED"} for t in PILOT],
        "decisions": decisions, "returned": len(decisions), "truncated": sum(counts.values()) > limit,
        "execution": {"status": "NOT_ENABLED", "fills": None, "pnl": None, "reward": None},
        "report_delivery": "APP_ONLY; external delivery is not enabled",
    }
