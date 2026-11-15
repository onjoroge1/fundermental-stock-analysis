"""The office work queue: one row per logical piece of work, one run row per attempt.

Guarantees, each covered by a test against real Postgres:

* Enqueueing the same ``logical_key`` twice makes one task.
* Claiming is one transaction under an advisory lock with ``FOR UPDATE SKIP LOCKED``, so two workers
  never hold the same task, and no more than the concurrency limit run at once.
* A lease that expires is recovered: the task waits and is retried, up to ``max_attempts``, then FAILED.
* A run that had an external (paid) call open when its lease expired is never sent again automatically;
  its task becomes BLOCKED for a person to look at.
* Only the holder of the current lease can finish a task. A late worker gets ``LeaseLost``.
* Error codes are short upper-case codes, never free text, so no message or secret is stored.

Times come from the database clock (``now()``), so workers with different clocks agree.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .. import db
from . import config

CLAIM_LOCK = 0x0FF1CE01  # advisory lock that serialises claiming and lease recovery
WAITING_STATES = ("WAITING_DATA", "WAITING_REVIEW")
REQUEUE_FROM = (*WAITING_STATES, "BLOCKED")
CANCEL_FROM = ("QUEUED", "RETRY_WAIT", *WAITING_STATES, "BLOCKED")
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,79}$")


class LeaseLost(RuntimeError):
    """The caller no longer holds the task: its lease expired and was recovered, or it never held it."""


@dataclass(frozen=True)
class Lease:
    task_id: str
    run_id: str
    lease_token: str
    attempt: int
    logical_key: str
    task_type: str
    payload: dict


def connect():
    return db.connect()


def _code(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_CODE.match(value):
        raise ValueError("error_code must be a short upper-case code such as PROVIDER_TIMEOUT")
    return value


def _json(value: Any, name: str) -> Jsonb:
    value = {} if value is None else value
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    if len(json.dumps(value, sort_keys=True, default=str)) > config.MAX_JSON_BYTES:
        raise ValueError(f"{name} is too large for the queue; store the document elsewhere and pass its id")
    return Jsonb(value)


# --------------------------------------------------------------------------------------------- enqueue

def enqueue(logical_key: str, task_type: str, payload: dict | None = None, *, priority: int = 100,
            due_at=None, max_attempts: int | None = None, depends_on: tuple[str, ...] = ()) -> dict:
    """Add a task, or return the existing one with the same ``logical_key``.

    ``depends_on`` lists task ids that must be COMPLETED first. Dependencies are only recorded when the task
    is created, and only on tasks that already exist, so a cycle cannot be built.
    """
    config.require_enabled()
    task_id = str(uuid4())
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO office_tasks(task_id, logical_key, task_type, payload, priority, due_at, max_attempts)
               VALUES (%s, %s, %s, %s, %s, COALESCE(%s, now()), %s)
               ON CONFLICT (logical_key) DO NOTHING RETURNING task_id""",
            (task_id, logical_key, task_type, _json(payload, "payload"), priority, due_at,
             max_attempts or config.DEFAULT_MAX_ATTEMPTS)).fetchone()
        if row is None:
            existing = conn.execute("SELECT task_id, state FROM office_tasks WHERE logical_key = %s",
                                    (logical_key,)).fetchone()
            return {"task_id": str(existing[0]), "created": False, "state": existing[1]}
        for parent in dict.fromkeys(str(UUID(str(p))) for p in depends_on):
            conn.execute("INSERT INTO office_task_dependencies(task_id, depends_on) VALUES (%s, %s)",
                         (task_id, parent))
    return {"task_id": task_id, "created": True, "state": "QUEUED"}


# ----------------------------------------------------------------------------------------------- claim

def _recover(cur) -> dict:
    """Close expired leases and block tasks whose dependency can no longer complete. Caller holds CLAIM_LOCK."""
    counts = {"retry": 0, "failed": 0, "blocked_uncertain": 0, "blocked_dependency": 0}
    cur.execute(
        """SELECT t.task_id, t.lease_token, t.attempts, t.max_attempts, COALESCE(r.external_call_open, false)
           FROM office_tasks t LEFT JOIN office_runs r ON r.lease_token = t.lease_token
           WHERE t.state = 'RUNNING' AND t.lease_until < now() FOR UPDATE OF t""")
    for task_id, token, attempts, max_attempts, call_open in cur.fetchall():
        if call_open:
            state, code, delay, key = "BLOCKED", "UNCERTAIN_EXTERNAL_CALL", None, "blocked_uncertain"
        elif attempts >= max_attempts:
            state, code, delay, key = "FAILED", "LEASE_EXPIRED_MAX_ATTEMPTS", None, "failed"
        else:
            state, code, delay, key = "RETRY_WAIT", "LEASE_EXPIRED", config.retry_delay_seconds(attempts), "retry"
        cur.execute(
            """UPDATE office_tasks SET state = %s, error_code = %s, lease_token = NULL, lease_owner = NULL,
                   lease_until = NULL, retry_at = now() + make_interval(secs => %s::double precision), updated_at = now()
               WHERE task_id = %s""", (state, code, delay, task_id))
        cur.execute(
            """UPDATE office_runs SET finished_at = now(), outcome = 'LEASE_EXPIRED', error_code = %s
               WHERE lease_token = %s AND finished_at IS NULL""", (code, token))
        counts[key] += 1
    cur.execute(
        """UPDATE office_tasks t SET state = 'BLOCKED', error_code = 'DEPENDENCY_NOT_COMPLETED',
               retry_at = NULL, updated_at = now()
           WHERE t.state IN ('QUEUED','RETRY_WAIT') AND EXISTS (
               SELECT 1 FROM office_task_dependencies d JOIN office_tasks p ON p.task_id = d.depends_on
               WHERE d.task_id = t.task_id AND p.state IN ('FAILED','CANCELLED'))""")
    counts["blocked_dependency"] = cur.rowcount
    return counts


def recover_expired() -> dict:
    """Recover expired leases without claiming anything. Safe to call at any time, by anyone."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (CLAIM_LOCK,))
        return _recover(cur)


def claim(actor: str, *, task_types: tuple[str, ...] | None = None, lease_seconds: int | None = None,
          max_concurrent: int | None = None, code_version: str = "", config_version: str = "",
          github_run_id: str | None = None) -> Lease | None:
    """Take the next runnable task, or return None when there is none or the office is at its limit."""
    config.require_enabled()
    seconds = lease_seconds or config.DEFAULT_LEASE_SECONDS
    if not 1 <= seconds <= config.MAX_LEASE_SECONDS:
        raise ValueError("lease_seconds out of range")
    limit = config.MAX_CONCURRENT_TASKS if max_concurrent is None else max_concurrent
    # On a GitHub runner every run is stamped with the workflow run and commit, so it can be matched later.
    github_run_id = github_run_id or (os.environ.get("GITHUB_RUN_ID", "").strip() or None)
    code_version = code_version or os.environ.get("GITHUB_SHA", "").strip()[:40]
    token, run_id = str(uuid4()), str(uuid4())
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (CLAIM_LOCK,))
        _recover(cur)
        cur.execute("SELECT count(*) FROM office_tasks WHERE state = 'RUNNING'")
        if cur.fetchone()[0] >= limit:
            return None
        cur.execute(
            """WITH pick AS (
                   SELECT t.task_id FROM office_tasks t
                   WHERE ((t.state = 'QUEUED' AND t.due_at <= now())
                          OR (t.state = 'RETRY_WAIT' AND t.retry_at <= now()))
                     AND t.attempts < t.max_attempts
                     AND (%(types)s::text[] IS NULL OR t.task_type = ANY(%(types)s::text[]))
                     AND NOT EXISTS (
                         SELECT 1 FROM office_task_dependencies d
                         JOIN office_tasks p ON p.task_id = d.depends_on
                         WHERE d.task_id = t.task_id AND p.state <> 'COMPLETED')
                   ORDER BY t.priority, t.due_at, t.created_at
                   FOR UPDATE OF t SKIP LOCKED LIMIT 1)
               UPDATE office_tasks t SET state = 'RUNNING', attempts = t.attempts + 1, lease_owner = %(actor)s,
                   lease_token = %(token)s, lease_until = now() + make_interval(secs => %(seconds)s::double precision),
                   retry_at = NULL, error_code = NULL, updated_at = now()
               FROM pick WHERE t.task_id = pick.task_id
               RETURNING t.task_id, t.logical_key, t.task_type, t.payload""",
            {"types": list(task_types) if task_types else None, "actor": actor, "token": token,
             "seconds": seconds})
        row = cur.fetchone()
        if row is None:
            return None
        cur.execute(
            """INSERT INTO office_runs(run_id, task_id, attempt, actor, lease_token, code_version,
                   config_version, github_run_id)
               SELECT %s, %s, COALESCE(max(attempt), 0) + 1, %s, %s, %s, %s, %s
               FROM office_runs WHERE task_id = %s RETURNING attempt""",
            (run_id, row[0], actor, token, code_version, config_version, github_run_id, row[0]))
        attempt = cur.fetchone()[0]
    return Lease(str(row[0]), run_id, token, attempt, row[1], row[2], row[3])


# ---------------------------------------------------------------------------------------- lease holder

def heartbeat(lease: Lease, *, lease_seconds: int | None = None) -> None:
    seconds = lease_seconds or config.DEFAULT_LEASE_SECONDS
    with connect() as conn:
        held = conn.execute(
            """UPDATE office_tasks SET lease_until = now() + make_interval(secs => %s::double precision), updated_at = now()
               WHERE task_id = %s AND lease_token = %s AND state = 'RUNNING' RETURNING task_id""",
            (seconds, lease.task_id, lease.lease_token)).fetchone()
        if held is None:
            raise LeaseLost("LEASE_LOST")
        conn.execute("UPDATE office_runs SET heartbeat_at = now() WHERE run_id = %s", (lease.run_id,))


def set_external_call(lease: Lease, open_: bool) -> None:
    """Mark that a paid or otherwise unrepeatable call is in flight (True) or has been recorded (False).

    Set it True, in its own committed transaction, before the call leaves the process. If the worker then
    dies, recovery blocks the task instead of sending the call a second time.
    """
    with connect() as conn:
        done = conn.execute(
            """UPDATE office_runs r SET external_call_open = %s FROM office_tasks t
               WHERE r.run_id = %s AND r.finished_at IS NULL AND t.task_id = r.task_id
                 AND t.lease_token = r.lease_token AND t.state = 'RUNNING' RETURNING r.run_id""",
            (bool(open_), lease.run_id)).fetchone()
        if done is None:
            raise LeaseLost("LEASE_LOST")


def _finish(lease: Lease, *, state: str, outcome: str, error_code: str | None = None, result=None,
            retry: bool = False, refund_attempt: bool = False, usage=None, cost_usd=None,
            model_version: str = "") -> dict:
    with connect() as conn:
        held = conn.execute(
            """SELECT attempts, max_attempts FROM office_tasks
               WHERE task_id = %s AND lease_token = %s AND state = 'RUNNING' FOR UPDATE""",
            (lease.task_id, lease.lease_token)).fetchone()
        if held is None:
            raise LeaseLost("LEASE_LOST")
        attempts, max_attempts = held
        delay = None
        if retry:
            if attempts < max_attempts:
                state, outcome, delay = "RETRY_WAIT", "RETRY", config.retry_delay_seconds(attempts)
            else:
                state, outcome = "FAILED", "FAILED"
        conn.execute(
            """UPDATE office_tasks SET state = %s, error_code = %s, result = %s, lease_token = NULL,
                   lease_owner = NULL, lease_until = NULL, retry_at = now() + make_interval(secs => %s::double precision),
                   attempts = attempts - %s, updated_at = now()
               WHERE task_id = %s""",
            (state, error_code, None if result is None else _json(result, "result"), delay,
             1 if refund_attempt else 0, lease.task_id))
        conn.execute(
            """UPDATE office_runs SET finished_at = now(), heartbeat_at = now(), outcome = %s, error_code = %s,
                   external_call_open = false, usage = %s, cost_usd = %s, model_version = %s
               WHERE run_id = %s AND finished_at IS NULL""",
            (outcome, error_code, _json(usage, "usage"), cost_usd, model_version, lease.run_id))
    return {"task_id": lease.task_id, "state": state, "attempt": lease.attempt}


def complete(lease: Lease, result: dict | None = None, *, usage: dict | None = None, cost_usd=None,
             model_version: str = "") -> dict:
    return _finish(lease, state="COMPLETED", outcome="COMPLETED", result=result or {}, usage=usage,
                   cost_usd=cost_usd, model_version=model_version)


def fail(lease: Lease, error_code: str, *, retryable: bool = True, usage: dict | None = None,
         cost_usd=None) -> dict:
    """Record a failed attempt. A retryable failure waits and runs again until ``max_attempts`` is used up."""
    return _finish(lease, state="FAILED", outcome="FAILED", error_code=_code(error_code), retry=retryable,
                   usage=usage, cost_usd=cost_usd)


def block(lease: Lease, error_code: str) -> dict:
    """Stop the task until a person requeues it. Used when retrying could repeat an unsafe step."""
    return _finish(lease, state="BLOCKED", outcome="BLOCKED", error_code=_code(error_code))


def wait(lease: Lease, state: str, error_code: str | None = None) -> dict:
    """Park the task until data or a review arrives. Waiting does not use up an attempt."""
    if state not in WAITING_STATES:
        raise ValueError("state must be WAITING_DATA or WAITING_REVIEW")
    return _finish(lease, state=state, outcome="WAITING", refund_attempt=True,
                   error_code=None if error_code is None else _code(error_code))


# ----------------------------------------------------------------------------------- controller actions

def requeue(task_id: str, *, reset_attempts: bool = False) -> bool:
    """Return a waiting or blocked task to the queue. Only a person may pass ``reset_attempts``."""
    with connect() as conn:
        row = conn.execute(
            """UPDATE office_tasks SET state = 'QUEUED', error_code = NULL, due_at = now(), updated_at = now(),
                   attempts = CASE WHEN %s THEN 0 ELSE attempts END
               WHERE task_id = %s AND state = ANY(%s) RETURNING task_id""",
            (reset_attempts, task_id, list(REQUEUE_FROM))).fetchone()
    return row is not None


def cancel(task_id: str) -> bool:
    """Cancel a task that is not running or finished. A running task ends through its own lease."""
    with connect() as conn:
        row = conn.execute(
            """UPDATE office_tasks SET state = 'CANCELLED', retry_at = NULL, updated_at = now()
               WHERE task_id = %s AND state = ANY(%s) RETURNING task_id""",
            (task_id, list(CANCEL_FROM))).fetchone()
    return row is not None


def get(task_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            """SELECT task_id, logical_key, task_type, state, attempts, max_attempts, error_code, result,
                      lease_owner, lease_until, retry_at
               FROM office_tasks WHERE task_id = %s""", (task_id,)).fetchone()
    if row is None:
        return None
    keys = ("task_id", "logical_key", "task_type", "state", "attempts", "max_attempts", "error_code",
            "result", "lease_owner", "lease_until", "retry_at")
    return {**dict(zip(keys, row)), "task_id": str(row[0])}


def counts() -> dict:
    """Tasks by state, for status pages."""
    with connect() as conn:
        return dict(conn.execute("SELECT state, count(*) FROM office_tasks GROUP BY state").fetchall())


# ------------------------------------------------------------------------------------ agent definitions

def register_definition(role: str, version: int, provider: str, model: str, definition: dict) -> dict:
    """Record one immutable version of a role. Re-registering identical content is a no-op; different
    content under an existing version is refused, so a prompt or tool list cannot change silently."""
    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO office_agent_definitions(role, version, provider, model, definition,
                   definition_sha256) VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (role, version) DO NOTHING RETURNING role""",
            (role, version, provider, model, _json(definition, "definition"), digest)).fetchone()
        if row is None:
            stored = conn.execute(
                """SELECT provider, model, definition_sha256 FROM office_agent_definitions
                   WHERE role = %s AND version = %s""", (role, version)).fetchone()
            if tuple(stored) != (provider, model, digest):
                raise ValueError("DEFINITION_VERSION_ALREADY_REGISTERED_WITH_DIFFERENT_CONTENT")
    return {"role": role, "version": version, "definition_sha256": digest, "created": row is not None}
