"""One bounded pass of office work, for a GitHub runner or a person at a terminal.

    python -m stock_machine.office.worker --mode observe
    python -m stock_machine.office.worker --mode work [--trigger schedule] [--require-research]
    python -m stock_machine.office.worker --mode resolve --call-not-made --task-ids ID[,ID]
    python -m stock_machine.office.worker --mode requeue --cause-fixed --task-ids ID[,ID]

``observe`` only reads. It prints the same summary the owner's console shows and works while the office
is off. ``work`` does one pass and stops:

1. record the role definitions and the strategy inventory (both are written once);
2. apply the admission checks to every candidate and record each decision with its date;
3. turn new filings and company events into office events, and queue at most three new memos a day;
4. run a bounded number of memo and review tasks, if a model provider and a model have been chosen;
5. if the paper book is switched on: fill due orders, close due positions, scan each deployed rule for
   the latest price session, mark the book, score it, and apply the pause or retire policy;
6. build the morning briefing after 08:15 New York time, and the weekly one after 19:30 on a Friday.

Every step is safe to repeat. A pass is one office task with a stable key: a scheduled pass is keyed by
its half-hour slot, a manual one by its GitHub run. Starting the same slot or run again continues that
same task, it never makes a second one, and the receipt printed is the task that was actually claimed.

What the exit status means, so that GitHub shows a real problem in red:

* 0: the pass completed, or there was deliberately nothing to do (office off, outside office hours, slot
  already worked);
* 1: the database is not at the office revision, a step failed, the slot's task has failed for good, or
  ``--require-research`` was given and no memo task completed cleanly.

This module applies no migration, places no order, connects to no broker and reads no secret. It prints
counts and short codes only: never an exception's text, a connection string or a model's words.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime, time as clock
from zoneinfo import ZoneInfo

from . import admission, budget, config, inventory, paper, providers, qa, research, roles, scout, status, tasks

ACTOR = "office-worker"
PULSE_TASK = "worker_pulse"
MEMOS_PER_DAY = 3                      # the build brief's pilot ceiling for new company memos
MAX_TASKS = 4
MAX_SECONDS = 600
NEW_YORK = ZoneInfo("America/New_York")
_SAFE = re.compile(r"^[A-Z][A-Z0-9_:.-]{2,119}$")
SLOT_MINUTES = 30
OFFICE_HOURS = (clock(7, 0), clock(20, 15))          # New York, weekdays; scheduled passes outside these do nothing
MORNING_BRIEFING_FROM = clock(8, 15)
WEEKLY_BRIEFING_FROM = clock(19, 30)                # Fridays
SUCCESS = ("COMPLETED", "COMPLETED_WITH_ATTENTION", "OFFICE_OFF", "OUTSIDE_OFFICE_HOURS", "SLOT_ALREADY_COMPLETED",
           "SLOT_IN_PROGRESS", "SLOT_RETRY_NOT_DUE")


def safe_code(error: BaseException) -> str:
    """An error as a short code. Anything that is not already a code becomes INTERNAL_ERROR."""
    text = str(getattr(error, "code", "") or error)
    return text if _SAFE.match(text) else "INTERNAL_ERROR"


class Skip(Exception):
    """A step that had nothing it was allowed to do. Not a failure."""


def observe() -> dict:
    return {"mode": "observe", **status.summary()}


def _schema_ready() -> bool:
    with tasks.connect() as conn:
        if not conn.execute("SELECT to_regclass('alembic_version') IS NOT NULL").fetchone()[0]:
            return False
        return [r[0] for r in conn.execute("SELECT version_num FROM alembic_version")] == [config.OFFICE_SCHEMA_VERSION]


def _queue_memos() -> dict:
    with tasks.connect() as conn:
        today = conn.execute(
            """SELECT count(*) FROM office_tasks WHERE task_type = %s AND logical_key LIKE 'memo:%%:v1'
               AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""",
            (scout.TASK_ANALYST,)).fetchone()[0]
    if today >= MEMOS_PER_DAY:
        raise Skip("DAILY_MEMO_LIMIT_REACHED")
    return {"queued": len(scout.queue_memos(limit=MEMOS_PER_DAY - today)), "already_today": today}


def _research(max_tasks: int, deadline: float, provider) -> dict:
    if provider is None and (config.provider() == "none" or not config.model()):
        raise Skip("PROVIDER_NOT_CONFIGURED")
    if provider is None:
        # Before any task is claimed: a provider that cannot send at all (no key, no SDK) fails this step
        # with its code and leaves every memo task untouched in the queue.
        ready = getattr(providers.get(), "ready", None)
        if ready is not None:
            ready()
    states: dict[str, int] = {}
    for _ in range(max_tasks):
        if time.monotonic() >= deadline:
            break
        result = research.work_one(ACTOR, provider)
        if result is None:
            break
        state = str(result.get("state", "UNKNOWN"))
        states[state] = states.get(state, 0) + 1
    done = states.get("COMPLETED", 0)
    return {"tasks": sum(states.values()), "states": states, "completed": done,
            "needing_attention": sum(states.values()) - done}


def _briefings(now: datetime) -> dict:
    """Briefings are built last in a pass, and only once their time in New York has come."""
    local = now.astimezone(NEW_YORK)
    kinds = (["MORNING"] if local.time() >= MORNING_BRIEFING_FROM else []) + (
        ["WEEKLY"] if local.weekday() == 4 and local.time() >= WEEKLY_BRIEFING_FROM else [])
    if not kinds:
        raise Skip("BRIEFING_NOT_DUE")
    return {kind.lower() + "_created": research.build_briefing(kind, local.date())["created"] for kind in kinds}


def pulse_key(now: datetime, trigger: str) -> str:
    """The stable name of this pass. A schedule is keyed by its half-hour slot, so a late, repeated or
    re-run trigger for the same slot is the same task. A manual run is keyed by its GitHub run."""
    if trigger == "schedule":
        utc = now.astimezone(UTC)
        return f"pulse:slot:{utc:%Y%m%dT%H}{utc.minute // SLOT_MINUTES * SLOT_MINUTES:02d}"
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    return f"pulse:manual:{run_id}" if run_id.isdigit() else f"pulse:local:{now:%Y%m%dT%H%M%S}"


def in_office_hours(now: datetime) -> bool:
    local = now.astimezone(NEW_YORK)
    return local.weekday() < 5 and OFFICE_HOURS[0] <= local.time() < OFFICE_HOURS[1]


def _paper_targets() -> tuple[object, list[str], list[tuple[str, str, str, str]]]:
    with tasks.connect() as conn:
        session = conn.execute("SELECT max(date) FROM prices_daily WHERE date <= current_date").fetchone()[0]
        books = [r[0] for r in conn.execute("SELECT book_id FROM office_books ORDER BY book_id")]
        deployments = [(str(r[0]), r[1], r[2], r[3]) for r in conn.execute(
            """SELECT deployment_id, strategy_id, version, book_id FROM office_deployments
               WHERE execution_kind = 'NATIVE' AND state IN ('EXPERIMENTAL_PAPER','PAUSED')
               ORDER BY created_at, deployment_id""")]
    return session, books, deployments


def _paper() -> dict:
    """Entries, exits, scans and marks for the latest price session. Everything here is simulated."""
    if not config.paper_enabled():
        raise Skip("PAPER_OFF")
    session, books, deployments = _paper_targets()
    if session is None:
        raise Skip("NO_PRICE_SESSION")
    out = {"session": str(session), "filled": 0, "closed": 0, "scans": {}, "marks": 0, "needs_review": 0}
    for book in books:
        out["filled"] += paper.fill_due(book)["filled"]
        closed = paper.close_due(book)
        out["closed"] += closed["closed"]
        out["needs_review"] += len(closed["needs_review"])
    for deployment_id, strategy_id, version, _ in deployments:
        if (strategy_id, version) not in paper.RULES:
            code = "NO_RULE_REGISTERED"
        else:
            try:
                paper.scan(deployment_id, session)
                code = "SCANNED"
            except ValueError as error:
                code = safe_code(error)
        out["scans"][code] = out["scans"].get(code, 0) + 1
    for book in books:
        out["marks"] += paper.mark(book, session)["created"]
    return out


def _qa() -> dict:
    """Score each native deployment for the latest price session, then apply the pause or retire policy."""
    if not config.paper_enabled():
        raise Skip("PAPER_OFF")
    session, _, deployments = _paper_targets()
    if session is None:
        raise Skip("NO_PRICE_SESSION")
    verdicts: dict[str, int] = {}
    changed = 0
    for deployment_id, *_ in deployments:
        scored = qa.evaluate(deployment_id, session)
        for kind in ("operational", "strategy"):
            verdict = scored[kind]["verdict"]
            verdicts[verdict] = verdicts.get(verdict, 0) + 1
            changed += bool(admission.apply_evaluation(scored[kind]["evaluation_id"]).get("changed"))
    return {"session": str(session), "deployments": len(deployments), "verdicts": verdicts, "state_changes": changed}


def work(*, max_tasks: int = MAX_TASKS, max_seconds: int = MAX_SECONDS, provider=None, now: datetime | None = None,
         trigger: str = "manual", require_research: bool = False) -> dict:
    """One pass. Returns a summary of counts and codes; raises nothing for an ordinary failure."""
    out: dict = {"mode": "work", "trigger": trigger, "steps": {}}
    if not config.enabled():
        return {**out, "state": "OFFICE_OFF"}
    now = now or datetime.now(UTC)
    if trigger == "schedule" and not in_office_hours(now):
        return {**out, "state": "OUTSIDE_OFFICE_HOURS"}
    if not _schema_ready():
        return {**out, "state": "SCHEMA_NOT_READY"}
    deadline = time.monotonic() + max_seconds
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    run_id = run_id if run_id.isdigit() else None
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "").strip()
    key = pulse_key(now, trigger)
    queued = tasks.enqueue(key, PULSE_TASK, {"trigger": trigger})
    lease = tasks.claim(ACTOR, task_types=(PULSE_TASK,), logical_key=key, max_concurrent=1000,
                        lease_seconds=min(max_seconds + 300, config.MAX_LEASE_SECONDS), github_run_id=run_id,
                        code_version=os.environ.get("GITHUB_SHA", "")[:40])
    receipt = {"pulse": key, "task_id": queued["task_id"], "github_run_id": run_id,
               "github_run_attempt": int(attempt) if attempt.isdigit() else None}
    if lease is None:                                   # this slot or run already has its task: say where it stands
        current = (tasks.get(queued["task_id"]) or {}).get("state", "UNKNOWN")
        state = {"COMPLETED": "SLOT_ALREADY_COMPLETED", "RUNNING": "SLOT_IN_PROGRESS", "RETRY_WAIT": "SLOT_RETRY_NOT_DUE",
                 "QUEUED": "SLOT_RETRY_NOT_DUE"}.get(current, "SLOT_FAILED")
        return {**out, **receipt, "state": state}
    receipt.update(run_id=lease.run_id, attempt=lease.attempt)
    steps = (("roles", lambda: {"created": sum(r["created"] for r in roles.register_all(config.provider(), config.model()))}),
             ("inventory", inventory.load),
             ("admissions", admission.decide_candidates),
             ("scout", scout.scan),
             ("queue_memos", _queue_memos),
             ("research", lambda: _research(max_tasks, deadline, provider)),
             ("paper", _paper),
             ("qa", _qa),
             ("briefings", lambda: _briefings(now)))
    failed = False
    for name, step in steps:
        if time.monotonic() >= deadline:
            out["steps"][name] = {"skipped": "OUT_OF_TIME"}
            continue
        try:
            out["steps"][name] = {"ok": True, **step()}
        except Skip as skip:
            out["steps"][name] = {"skipped": str(skip)}
        except Exception as error:                                   # recorded as a code; the pass goes on
            failed = True
            out["steps"][name] = {"ok": False, "code": safe_code(error)}
    researched = out["steps"].get("research", {})
    canary_failed = require_research and not (researched.get("ok") and researched.get("completed", 0) >= 1
                                              and researched.get("needing_attention") == 0)
    if failed:
        out["state"] = "COMPLETED_WITH_ERRORS"
    elif canary_failed:
        out["state"] = "CANARY_FAILED"
    elif researched.get("needing_attention"):
        out["state"] = "COMPLETED_WITH_ATTENTION"
    else:
        out["state"] = "COMPLETED"
    try:
        if failed:
            tasks.fail(lease, "STEP_FAILED")                         # the same slot or run may be tried again
        elif canary_failed:
            tasks.fail(lease, "CANARY_FAILED", retryable=False)
        else:
            tasks.complete(lease, {"state": out["state"], "steps": out["steps"], **receipt})
    except tasks.LeaseLost:
        out["state"] = "LEASE_LOST"
    return {**out, **receipt}


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def resolve(task_ids: list[str]) -> dict:
    """Owner action: these blocked tasks' uncertain calls were never sent. Release their reservations and
    queue the tasks again. Refused as a whole unless every id is well formed."""
    config.require_enabled()
    if not task_ids or len(task_ids) > 20 or any(not _UUID.match(t) for t in task_ids):
        return {"mode": "resolve", "state": "TASK_IDS_INVALID"}
    run = os.environ.get("GITHUB_RUN_ID", "").strip()
    reference = f"github-run:{run}" if run.isdigit() else "local"
    done, refused = [], 0
    for task_id in dict.fromkeys(task_ids):
        try:
            done.append(budget.resolve_not_made(task_id, reference=reference))
        except ValueError:
            refused += 1
    return {"mode": "resolve", "state": "RESOLVED" if not refused else "RESOLVED_WITH_REFUSALS",
            "resolved": done, "refused": refused}


def requeue(task_ids: list[str]) -> dict:
    """Owner action: the cause of a provider's rejection has been fixed, so queue these blocked tasks again.
    Only tasks blocked with a provider-rejection code move; nothing was billed for them and no reservation
    is touched. Refused as a whole unless every id is well formed. It calls no model."""
    config.require_enabled()
    if not task_ids or len(task_ids) > 20 or any(not _UUID.match(t) for t in task_ids):
        return {"mode": "requeue", "state": "TASK_IDS_INVALID"}
    done, refused = [], 0
    for task_id in dict.fromkeys(task_ids):
        was = tasks.requeue_rejected(task_id)
        if was is None:
            refused += 1
        else:
            done.append({"task_id": task_id, "was_blocked_with": was, "state": "QUEUED"})
    return {"mode": "requeue", "state": "REQUEUED" if not refused else "REQUEUED_WITH_REFUSALS",
            "requeued": done, "refused": refused}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One bounded pass of office work.")
    parser.add_argument("--mode", choices=["observe", "work", "resolve", "requeue"], default="observe")
    parser.add_argument("--task-ids", default="", help="resolve, requeue: comma-separated ids of blocked tasks")
    parser.add_argument("--cause-fixed", action="store_true",
                        help="requeue: the owner states the cause of the provider's rejection has been fixed")
    parser.add_argument("--call-not-made", action="store_true",
                        help="resolve: the owner states these calls were never sent")
    parser.add_argument("--trigger", choices=["manual", "schedule"], default="manual")
    parser.add_argument("--require-research", action="store_true",
                        help="canary: fail unless at least one memo task completed and none needs attention")
    parser.add_argument("--max-tasks", type=int, default=MAX_TASKS, choices=range(0, 11), metavar="0..10")
    parser.add_argument("--max-seconds", type=int, default=MAX_SECONDS, choices=range(30, 1801), metavar="30..1800")
    args = parser.parse_args(argv)
    try:
        if args.mode == "resolve":
            result = resolve([t.strip() for t in args.task_ids.split(",") if t.strip()]) if args.call_not_made else {
                "mode": "resolve", "state": "STATEMENT_REQUIRED"}
        elif args.mode == "requeue":
            result = requeue([t.strip() for t in args.task_ids.split(",") if t.strip()]) if args.cause_fixed else {
                "mode": "requeue", "state": "STATEMENT_REQUIRED"}
        else:
            result = observe() if args.mode == "observe" else work(
                max_tasks=args.max_tasks, max_seconds=args.max_seconds, trigger=args.trigger,
                require_research=args.require_research)
    except Exception as error:
        print(json.dumps({"mode": args.mode, "state": "FAILED", "code": safe_code(error)}))
        return 1
    print(json.dumps(result, indent=1, sort_keys=True, default=str))
    return 0 if args.mode == "observe" or result.get("state") in (*SUCCESS, "RESOLVED", "REQUEUED") else 1


if __name__ == "__main__":
    sys.exit(main())
