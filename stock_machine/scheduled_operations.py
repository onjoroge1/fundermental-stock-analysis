"""Clock-gated agent operations with durable, owner-visible stage receipts."""

from datetime import datetime, timezone
from . import db
from .market_calendar import EASTERN, latest_completed_session
from .admin_panel import store

STAGES = ("ingestion", "paper", "learning", "progress")


def schedule(stage, now=None, *, workflow_cron=None, manual=False):
    if stage not in STAGES:
        raise ValueError("AGENT_STAGE_NOT_SUPPORTED")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("AGENT_STAGE_TIMESTAMP_NOT_AWARE")
    local = now.astimezone(EASTERN)
    session = latest_completed_session(now)
    slot = local.strftime("%H:%M")
    if manual:
        due, reason = True, "MANUAL_REPAIR"
        slot = "manual:" + now.isoformat()
    elif stage == "progress":
        due = local.weekday() < 5 and local.hour == 8 and local.minute >= 15
        reason = "DUE" if due else "OUTSIDE_PROGRESS_WINDOW"
        slot = "08:15"
    elif session != local.date().isoformat():
        due, reason = False, "NO_COMPLETED_SESSION_TODAY"
    elif stage == "ingestion":
        expected = (
            "30 21 * * 1-5"
            if local.utcoffset().total_seconds() == -4 * 3600
            else "30 22 * * 1-5"
        )
        due = (local.hour, local.minute) >= (17, 30) and workflow_cron in (
            None,
            expected,
        )
        reason = "DUE" if due else "OUTSIDE_INGESTION_WINDOW_OR_WRONG_DST_TRIGGER"
        slot = "17:30"
    elif stage == "paper":
        due = 17 <= local.hour < 23
        reason = "DUE" if due else "OUTSIDE_PAPER_WINDOW"
        slot = f"{local.hour:02d}:{local.minute//10*10:02d}"
    else:
        due = local.hour in (18, 20, 22) and local.minute >= 10
        reason = "DUE" if due else "OUTSIDE_LEARNING_WINDOW"
        slot = f"{local.hour:02d}:10"
    return {
        "stage": stage,
        "due": due,
        "reason": reason,
        "session": session,
        "local_time": local.isoformat(),
        "timezone": "America/New_York",
        "key": f"agent-stage:{stage}:{session}:{slot}",
    }


def paper_operation():
    from . import agent_trading

    if not store.controls()["capture_enabled"]:
        return {"status": "SKIPPED", "reason": "CAPTURE_PAUSED"}
    if agent_trading.get_mode()["mode"] != "PAPER":
        return {"status": "SKIPPED", "reason": "RESEARCH_MODE"}
    pending = agent_trading.process_pending(limit=6)
    exits = agent_trading.settle_holding_limits(limit=6)
    try:
        mark = agent_trading.mark_open_positions()
    except ValueError as exc:
        mark = {"status": "BLOCKED", "reason": str(exc)}
    status = (
        "ATTENTION"
        if any(
            r.get("status") in ("ATTENTION", "BLOCKED") for r in (pending, exits, mark)
        )
        else "OK"
    )
    return {
        "status": status,
        "pending_processed": pending.get("processed", 0),
        "positions_closed": exits.get("closed", 0),
        "pending": pending,
        "exits": exits,
        "mark_status": mark.get("status", "OK"),
        "mark_reason": mark.get("reason"),
        "pending_blocked": sum(
            r.get("status") == "BLOCKED" for r in pending.get("results", [])
        ),
        "exits_blocked": len(exits.get("blocked", [])),
        "broker_submission": False,
    }


def learning_operation():
    from .agent_intelligence.outcomes import score_matured
    from .agent_intelligence.shadow import score_matured as score_shadow

    # Independent failures are visible and do not prevent the other learner.
    results = {}
    for name, fn in (("paper", score_matured), ("shadow", score_shadow)):
        try:
            results[name] = fn(limit=100)
        except Exception as exc:
            results[name] = {"status": "FAILED", "reason_code": type(exc).__name__}
    return {
        "status": (
            "ATTENTION" if any(r["status"] != "OK" for r in results.values()) else "OK"
        ),
        "paper_scored": results["paper"].get("scored", 0),
        "shadow_scored": results["shadow"].get("scored", 0),
        "paper_blocked": results["paper"].get("blocked", 0),
        "shadow_blocked": sum(
            r.get("status") == "BLOCKED" for r in results["shadow"].get("results", [])
        ),
        "paper_status": results["paper"]["status"],
        "shadow_status": results["shadow"]["status"],
        "paper_reason_code": results["paper"].get("reason_code"),
        "shadow_reason_code": results["shadow"].get("reason_code"),
        **results,
        "promotion": "NOT_AUTHORIZED",
        "broker_submission": False,
    }


def run(stage, *, now=None, workflow_cron=None, manual=False, operation=None):
    plan = schedule(stage, now, workflow_cron=workflow_cron, manual=manual)
    if not plan["due"]:
        return {**plan, "status": "SKIPPED"}
    key = plan["key"]
    # Hold only a session lock, never an idle transaction across ingestion or
    # scoring. If a worker dies, its lock is released and the next call retries.
    with db.connect() as lock:
        lock.autocommit = True
        if not lock.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (key,)
        ).fetchone()[0]:
            return {**plan, "status": "BUSY"}
        try:
            prior = lock.execute(
                """SELECT details FROM operator_audit WHERE event='AGENT_STAGE_FINISHED'
                AND details->>'key'=%s AND details->>'status' IN ('OK','ATTENTION','SKIPPED')
                ORDER BY id DESC LIMIT 1""",
                (key,),
            ).fetchone()
            if prior:
                return {**prior[0], "replayed": True}
            store.audit(lock, "agent-scheduler", "AGENT_STAGE_STARTED", plan)
            started = datetime.now(timezone.utc).isoformat()
            if operation is None:
                if stage == "paper":
                    operation = paper_operation
                elif stage == "learning":
                    operation = learning_operation
                elif stage == "progress":
                    operation = progress_report
                else:
                    raise ValueError("INGESTION_RUNS_IN_GITHUB_ACTIONS")
            try:
                result = operation()
                if not isinstance(result, dict) or result.get("status") not in {
                    "OK",
                    "ATTENTION",
                    "SKIPPED",
                    "FAILED",
                }:
                    raise ValueError("AGENT_STAGE_RESULT_INVALID")
            except Exception as exc:
                result = {"status": "FAILED", "reason_code": type(exc).__name__}
            receipt = {
                **plan,
                **result,
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "broker_submission": False,
            }
            # Store counters, not raw portfolio/provider payloads or credentials.
            safe = {
                k: v
                for k, v in receipt.items()
                if k
                not in {
                    "paper",
                    "shadow",
                    "pending",
                    "exits",
                    "rows",
                    "stage_receipts",
                    "queue",
                    "shadow_pending",
                }
            }
            store.audit(lock, "agent-scheduler", "AGENT_STAGE_FINISHED", safe)
            return {**safe, "replayed": False}
        finally:
            lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (key,))


def progress_report():
    from .agents.contracts import AGENT_UNIVERSE

    session = latest_completed_session()
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = conn.execute(
            """SELECT c.ticker,p.day,s.max_record_date::text,s.status,
            f.as_of::text,f.status FROM companies c
            LEFT JOIN LATERAL (SELECT max(date)::text day FROM prices_daily WHERE ticker=c.ticker) p ON true
            LEFT JOIN LATERAL (SELECT max_record_date,status FROM dataset_snapshots
                WHERE ticker=c.ticker AND dataset='prices' ORDER BY last_checked_at DESC,observed_at DESC LIMIT 1) s ON true
            LEFT JOIN LATERAL (SELECT as_of,status FROM prediction_forecasts WHERE ticker=c.ticker
                ORDER BY as_of DESC,generated_at DESC LIMIT 1) f ON true
            WHERE c.ticker=ANY(%s)""",
            (list(AGENT_UNIVERSE),),
        ).fetchall()
        prices = sum(
            r[1] == session and r[2] == session and r[3] == "PASS" for r in rows
        )
        forecasts = sum(r[4] == session and r[5] == "OK" for r in rows)
        shadow = conn.execute(
            """SELECT (payload->>'horizon_sessions')::int,count(*),
            count(*) FILTER (WHERE payload->>'due_session'<=%s),min(payload->>'due_session')
            FROM research_evidence_records r WHERE kind='AGENT_SHADOW_SNAPSHOT_V1'
              AND NOT EXISTS (SELECT 1 FROM research_evidence_records o
                WHERE o.kind='AGENT_SHADOW_OUTCOME_V1' AND o.request_key=r.request_key)
            GROUP BY 1 ORDER BY 1""",
            (session,),
        ).fetchall()
        receipts = conn.execute(
            """SELECT DISTINCT ON (details->>'stage') details,event,occurred_at::text
            FROM operator_audit WHERE event IN ('AGENT_STAGE_STARTED','AGENT_STAGE_FINISHED')
            ORDER BY details->>'stage',id DESC"""
        ).fetchall()
        queue = dict(
            conn.execute(
                "SELECT status,count(*) FROM orchestration_jobs GROUP BY status"
            ).fetchall()
        )
        research = conn.execute(
            """SELECT count(DISTINCT ticker) FROM research_evidence_records
            WHERE kind='AGENT_INTELLIGENCE_V2' AND payload #>> '{state,as_of}'=%s AND ticker=ANY(%s)""",
            (session, list(AGENT_UNIVERSE)),
        ).fetchone()[0]
    stages = [
        {
            **r[0],
            "status": "UNFINISHED" if r[1] == "AGENT_STAGE_STARTED" else r[0]["status"],
            "recorded_at": r[2],
            "stale_session": r[0].get("session") != session,
        }
        for r in receipts
    ]
    attention = (
        prices != len(AGENT_UNIVERSE)
        or forecasts != len(AGENT_UNIVERSE)
        or any(
            (r["status"] in ("FAILED", "ATTENTION", "UNFINISHED") or r["stale_session"])
            for r in stages
            if r["stage"] != "progress"
        )
        or queue.get("FAILED", 0) > 0
        or any(r[2] for r in shadow)
    )
    return {
        "status": "ATTENTION" if attention else "OK",
        "latest_completed_session": session,
        "universe_count": len(AGENT_UNIVERSE),
        "prices_current": prices,
        "forecasts_current": forecasts,
        "research_observed": research,
        "shadow_pending_count": sum(r[1] for r in shadow),
        "shadow_due_count": sum(r[2] for r in shadow),
        "first_five_target_session": next((r[3] for r in shadow if r[0] == 5), None),
        "shadow_pending": [
            {"horizon": h, "pending": n, "due": due, "first_target_session": first}
            for h, n, due, first in shadow
        ],
        "stage_receipts": stages,
        "schedule_eastern": {
            "ingestion": "17:30",
            "paper": "every 10 minutes, 17:00–22:59",
            "learning": "18:10, 20:10, 22:10",
            "progress": "weekdays 08:15",
        },
        "research_status": (
            "COMPLETE" if research == len(AGENT_UNIVERSE) else "COLLECTING"
        ),
        "queue": queue,
        "broker_submission": False,
        "promotion": "NOT_AUTHORIZED",
    }
