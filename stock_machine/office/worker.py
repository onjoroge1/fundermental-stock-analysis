"""One bounded pass of office work, for a GitHub runner or a person at a terminal.

    python -m stock_machine.office.worker --mode observe
    python -m stock_machine.office.worker --mode work

``observe`` only reads. It prints the same summary the owner's console shows and works while the office
is off. ``work`` does one pass and stops:

1. record the role definitions and the strategy inventory (both are written once);
2. turn new filings and company events into office events, and queue at most three new memos a day;
3. run a bounded number of memo and review tasks, if a model provider and a model have been chosen;
4. build the morning briefing (and the weekly one on a Friday);
5. if the paper book is switched on: fill due orders, close due positions, scan each deployed rule for
   the latest price session, mark the book, score it, and apply the pause or retire policy.

Every step is safe to repeat, so a pass that is run twice or cut short adds nothing twice. The pass is
itself recorded as one office task with the GitHub run number, so a real run can be matched to a database
row. With the office switched off, ``work`` does nothing and says so.

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
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from . import admission, config, inventory, paper, qa, research, roles, scout, status, tasks

ACTOR = "office-worker"
PULSE_TASK = "worker_pulse"
MEMOS_PER_DAY = 3                      # the build brief's pilot ceiling for new company memos
MAX_TASKS = 4
MAX_SECONDS = 600
NEW_YORK = ZoneInfo("America/New_York")
_SAFE = re.compile(r"^[A-Z][A-Z0-9_:.-]{2,119}$")
SKIPPED = ("PROVIDER_NOT_CONFIGURED", "PAPER_OFF", "NO_PRICE_SESSION", "OUT_OF_TIME", "DAILY_MEMO_LIMIT_REACHED")


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
    states: dict[str, int] = {}
    for _ in range(max_tasks):
        if time.monotonic() >= deadline:
            break
        result = research.work_one(ACTOR, provider)
        if result is None:
            break
        state = str(result.get("state", "UNKNOWN"))
        states[state] = states.get(state, 0) + 1
    return {"tasks": sum(states.values()), "states": states}


def _briefings(now: datetime) -> dict:
    kinds = ["MORNING"] + (["WEEKLY"] if now.astimezone(NEW_YORK).weekday() == 4 else [])
    return {kind.lower() + "_created": research.build_briefing(kind)["created"] for kind in kinds}


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


def work(*, max_tasks: int = MAX_TASKS, max_seconds: int = MAX_SECONDS, provider=None, now: datetime | None = None) -> dict:
    """One pass. Returns a summary of counts and codes; raises nothing for an ordinary failure."""
    out: dict = {"mode": "work", "steps": {}}
    if not config.enabled():
        return {**out, "state": "OFFICE_OFF"}
    if not _schema_ready():
        return {**out, "state": "SCHEMA_NOT_READY"}
    now = now or datetime.now(UTC)
    deadline = time.monotonic() + max_seconds
    run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1").strip()
    key = f"pulse:{run_id}:{attempt}" if run_id.isdigit() and attempt.isdigit() else f"pulse:local:{now:%Y%m%dT%H%M%S}"
    tasks.enqueue(key, PULSE_TASK, {"max_tasks": max_tasks, "max_seconds": max_seconds})
    lease = tasks.claim(ACTOR, task_types=(PULSE_TASK,), max_concurrent=1000,
                        lease_seconds=min(max_seconds + 300, config.MAX_LEASE_SECONDS),
                        github_run_id=run_id if run_id.isdigit() else None,
                        code_version=os.environ.get("GITHUB_SHA", "")[:40])
    if lease is None:
        return {**out, "state": "PULSE_ALREADY_RECORDED"}
    steps = (("roles", lambda: {"created": sum(r["created"] for r in roles.register_all(config.provider(), config.model()))}),
             ("inventory", inventory.load),
             ("scout", scout.scan),
             ("queue_memos", _queue_memos),
             ("research", lambda: _research(max_tasks, deadline, provider)),
             ("briefings", lambda: _briefings(now)),
             ("paper", _paper),
             ("qa", _qa))
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
    out["state"] = "COMPLETED_WITH_ERRORS" if failed else "COMPLETED"
    try:
        if failed:
            tasks.fail(lease, "STEP_FAILED", retryable=False)
        else:
            tasks.complete(lease, {"steps": out["steps"]})
    except tasks.LeaseLost:
        out["state"] = "LEASE_LOST"
    return {**out, "pulse": key, "github_run_id": run_id if run_id.isdigit() else None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One bounded pass of office work.")
    parser.add_argument("--mode", choices=["observe", "work"], default="observe")
    parser.add_argument("--max-tasks", type=int, default=MAX_TASKS, choices=range(0, 11), metavar="0..10")
    parser.add_argument("--max-seconds", type=int, default=MAX_SECONDS, choices=range(30, 1801), metavar="30..1800")
    args = parser.parse_args(argv)
    try:
        result = observe() if args.mode == "observe" else work(max_tasks=args.max_tasks, max_seconds=args.max_seconds)
    except Exception as error:
        print(json.dumps({"mode": args.mode, "state": "FAILED", "code": safe_code(error)}))
        return 1
    print(json.dumps(result, indent=1, sort_keys=True, default=str))
    return 1 if result.get("state") in ("COMPLETED_WITH_ERRORS", "LEASE_LOST") else 0


if __name__ == "__main__":
    sys.exit(main())
