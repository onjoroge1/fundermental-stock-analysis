"""Fenced execution over the EXISTING orchestration_jobs table.

At-least-once computation; exactly-once immutable result publication per job.
An expired attempt cannot heartbeat, checkpoint, or publish over a newer one.
"""
from __future__ import annotations
from .contracts import canonical, digest

WORKER_ONLY = frozenset({"performance_report"})
MAX_ARTIFACT_BYTES = 1_000_000


class LeaseLost(RuntimeError):
    pass


def _json(value):
    from psycopg.types.json import Jsonb
    if len(canonical(value).encode()) > MAX_ARTIFACT_BYTES:
        raise ValueError("ARTIFACT_TOO_LARGE")
    return Jsonb(value)


def claim_next(conn, allowed_types=None):
    from .. import control_plane as cp
    cp.ensure_schema(conn)
    allowed = sorted((cp.JOB_TYPES - WORKER_ONLY) if allowed_types is None else set(allowed_types))
    if not allowed or not set(allowed) <= cp.JOB_TYPES:
        raise ValueError("WORKER_JOB_TYPES_INVALID")
    conn.execute("""UPDATE orchestration_jobs SET status='FAILED',lease_until=NULL,
        finished_at=now(),updated_at=now(),last_error='LEASE_EXPIRED_FINAL_ATTEMPT'
        WHERE job_type=ANY(%s) AND status='RUNNING' AND lease_until<clock_timestamp()
        AND attempts>=max_attempts""", (allowed,))
    found = conn.execute("""SELECT job_id FROM orchestration_jobs WHERE job_type=ANY(%s)
        AND status IN ('PENDING','RUNNING') AND attempts<max_attempts
        AND (lease_until IS NULL OR lease_until<clock_timestamp())
        ORDER BY created_at,job_id FOR UPDATE SKIP LOCKED LIMIT 1""", (allowed,)).fetchone()
    if not found:
        conn.commit()
        return None
    conn.execute("""UPDATE orchestration_jobs SET status='RUNNING',attempts=attempts+1,
        lease_until=clock_timestamp()+interval '15 minutes',started_at=COALESCE(started_at,now()),
        updated_at=now() WHERE job_id=%s""", (found[0],))
    conn.commit()
    return cp.get_job(conn, found[0])


def require_lease(conn, job):
    row = conn.execute("""SELECT job_id FROM orchestration_jobs WHERE job_id=%s AND attempts=%s
        AND status='RUNNING' AND lease_until>=clock_timestamp() FOR UPDATE""",
        (job["job_id"], job["attempts"])).fetchone()
    if not row:
        raise LeaseLost("WORKER_LEASE_LOST")


def heartbeat(conn, job):
    require_lease(conn, job)
    conn.execute("""UPDATE orchestration_jobs SET lease_until=clock_timestamp()+interval '15 minutes',
        updated_at=now() WHERE job_id=%s AND attempts=%s""", (job["job_id"], job["attempts"]))
    conn.commit()


def checkpoint(conn, job, value):
    from .. import research_store
    require_lease(conn, job)
    _json(value)
    payload = {"job_id": job["job_id"], "attempt": job["attempts"], "checkpoint": value}
    saved = research_store.save(conn, "WORKER_CHECKPOINT_V1", job["job_id"]+":"+digest(payload), payload, job.get("ticker"))
    conn.commit()
    return saved


def finish(conn, job, result):
    from .. import research_store, control_plane as cp
    require_lease(conn, job)
    _json(result)
    artifact = research_store.save(conn, "WORKER_ARTIFACT_V1", job["job_id"],
                                  {"job_id": job["job_id"], "result": result}, job.get("ticker"))
    body = {**result, "artifact_id": artifact["record_id"], "artifact_sha256": artifact["content_hash"]}
    conn.execute("""UPDATE orchestration_jobs SET status='SUCCEEDED',result=%s,last_error=NULL,
        lease_until=NULL,finished_at=now(),updated_at=now() WHERE job_id=%s AND attempts=%s""",
        (_json(body), job["job_id"], job["attempts"]))
    conn.commit()
    return cp.get_job(conn, job["job_id"])


def fail(conn, job, exc):
    from .. import control_plane as cp
    require_lease(conn, job)
    status = "PENDING" if job["attempts"] < job["max_attempts"] else "FAILED"
    code = type(exc).__name__[:80]
    conn.execute("""UPDATE orchestration_jobs SET status=%s,last_error=%s,
        lease_until=CASE WHEN %s='PENDING' THEN clock_timestamp()+interval '30 seconds' ELSE NULL END,
        finished_at=CASE WHEN %s='FAILED' THEN now() ELSE NULL END,updated_at=now()
        WHERE job_id=%s AND attempts=%s""", (status, code, status, status, job["job_id"], job["attempts"]))
    conn.commit()
    return cp.get_job(conn, job["job_id"])


def cancel(conn, job_id):
    row = conn.execute("""UPDATE orchestration_jobs SET status='CANCELLED',lease_until=NULL,
        finished_at=now(),updated_at=now() WHERE job_id=%s AND status IN ('PENDING','RUNNING') RETURNING job_id""",
        (job_id,)).fetchone()
    conn.commit()
    return {"job_id": job_id, "cancelled": bool(row), "note": "Completed side effects are not undone."}


def process_one():
    from .. import db, control_plane as cp
    with db.connect() as conn:
        job = claim_next(conn)
    if not job:
        return {"status": "IDLE", "message": "no runnable jobs"}
    try:
        result = cp.execute(job)
    except Exception as exc:
        with db.connect() as conn:
            try:
                return fail(conn, job, exc)
            except LeaseLost:
                return {"job_id": job["job_id"], "status": "LEASE_LOST"}
    with db.connect() as conn:
        try:
            return finish(conn, job, result)
        except LeaseLost:
            return {"job_id": job["job_id"], "status": "LEASE_LOST"}


def _child(payload, output):
    """Trusted, allowlisted analytics process. Not an arbitrary-code sandbox."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (240, 245))
        resource.setrlimit(resource.RLIMIT_FSIZE, (16_000_000, 16_000_000))
        resource.setrlimit(resource.RLIMIT_AS, (4_000_000_000, 4_000_000_000))
        from .reporting import build_report_job
        result = build_report_job(payload)
        _json(result)
        output.put((True, result))
    except Exception as exc:
        output.put((False, type(exc).__name__))


def run_analytics_once(max_seconds=300):
    from .. import db
    import multiprocessing as mp
    import queue
    import time
    if not 1 <= max_seconds <= 600:
        raise ValueError("WORKER_TIMEOUT_INVALID")
    with db.connect() as conn:
        job = claim_next(conn, WORKER_ONLY)
    if not job:
        return {"status": "IDLE"}
    ctx = mp.get_context("spawn")
    output = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_child, args=(job["payload"], output))
    proc.start()
    started, last_heartbeat = time.monotonic(), 0.0
    try:
        while time.monotonic()-started < max_seconds:
            if time.monotonic()-last_heartbeat >= 10:
                with db.connect() as conn:
                    heartbeat(conn, job)
                last_heartbeat = time.monotonic()
            try:
                ok, result = output.get(timeout=1)
                if not ok:
                    raise RuntimeError("ANALYTICS_CHILD_FAILED")
                with db.connect() as conn:
                    return finish(conn, job, result)
            except queue.Empty:
                if not proc.is_alive():
                    raise RuntimeError("ANALYTICS_CHILD_EXITED")
        raise TimeoutError("ANALYTICS_TIME_BUDGET_EXCEEDED")
    except LeaseLost:
        return {"job_id": job["job_id"], "status": "LEASE_LOST"}
    except Exception as exc:
        with db.connect() as conn:
            try:
                return fail(conn, job, exc)
            except LeaseLost:
                return {"job_id": job["job_id"], "status": "LEASE_LOST"}
    finally:
        if proc.is_alive(): proc.terminate()
        proc.join(timeout=5)
        if proc.is_alive(): proc.kill(); proc.join()
        output.close()


if __name__ == "__main__":
    import argparse
    import time
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        result = run_analytics_once()
        print(canonical({"job_id": result.get("job_id"), "status": result.get("status")}), flush=True)
        if args.once: break
        time.sleep(10)
