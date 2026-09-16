"""Append-only records with conflict detection, never mutable latest results."""
from __future__ import annotations

from psycopg.types.json import Jsonb

from .research_contract import digest


def save(conn, kind: str, request_key: str, payload: dict, ticker: str | None = None) -> dict:
    content_hash = digest(payload)
    record_id = "evidence_" + digest({"kind": kind, "request_key": request_key})
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO research_evidence_records
            (record_id,kind,ticker,request_key,content_hash,payload) VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT(kind,request_key) DO NOTHING""",
            (record_id, kind, ticker, request_key, content_hash, Jsonb(payload)))
        cur.execute("SELECT record_id,content_hash,payload,recorded_at::text FROM research_evidence_records WHERE kind=%s AND request_key=%s", (kind, request_key))
        row = cur.fetchone()
        if row[1] != content_hash:
            raise ValueError("Evidence request key already has different content")
    # Caller owns the transaction, including paired report/index/evidence work.
    return dict(zip(("record_id", "content_hash", "payload", "recorded_at"), row))


def get(conn, kind, request_key):
    with conn.cursor() as cur:
        cur.execute("SELECT record_id,content_hash,payload,recorded_at::text FROM research_evidence_records WHERE kind=%s AND request_key=%s", (kind, request_key))
        row = cur.fetchone()
    return dict(zip(("record_id", "content_hash", "payload", "recorded_at"), row)) if row else None


def latest(conn, kind, ticker=None):
    with conn.cursor() as cur:
        cur.execute("""SELECT record_id,content_hash,payload,recorded_at::text FROM research_evidence_records
            WHERE kind=%s AND (%s::text IS NULL OR ticker=%s) ORDER BY recorded_at DESC,record_id DESC LIMIT 1""", (kind, ticker, ticker))
        row = cur.fetchone()
    return dict(zip(("record_id", "content_hash", "payload", "recorded_at"), row)) if row else None


def records(conn, kind, *, limit=1000):
    if not 1 <= limit <= 5000:
        raise ValueError("record limit must be 1-5000")
    with conn.cursor() as cur:
        cur.execute("SELECT record_id,content_hash,payload,recorded_at::text FROM research_evidence_records WHERE kind=%s ORDER BY recorded_at,record_id LIMIT %s", (kind, limit))
        return [dict(zip(("record_id", "content_hash", "payload", "recorded_at"), row)) for row in cur.fetchall()]
