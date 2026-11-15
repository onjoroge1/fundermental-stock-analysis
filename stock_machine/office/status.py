"""A read-only summary of the office for the owner's console.

It only selects. It works while the office is off, and it says so plainly when the office tables have not
been created yet. It returns setting names and states, never a key or any other credential.
"""
from __future__ import annotations

from . import config, external, qa, tasks

TABLES = ("office_tasks", "office_runs", "office_budget_reservations", "office_events", "office_memos",
          "office_reviews", "office_briefings")
BOUNDARIES = {"broker_submission": False, "live_trading": False, "paper_book": "SIMULATED_ONLY"}


def _switches() -> dict:
    return {"office_enabled": config.enabled(), "paper_enabled": config.paper_enabled(),
            "provider": config.provider(), "model": config.model() or None,
            "daily_cap_usd": str(config.daily_usd_cap()), "monthly_cap_usd": str(config.monthly_usd_cap())}


def summary() -> dict:
    out = {"switches": _switches(), "boundaries": BOUNDARIES, "schema_version_needed": config.OFFICE_SCHEMA_VERSION}
    with tasks.connect() as conn:
        present = {name for name in TABLES
                   if conn.execute("SELECT to_regclass(%s) IS NOT NULL", (name,)).fetchone()[0]}
        if present != set(TABLES):
            return {**out, "schema": "NOT_MIGRATED", "tables_missing": sorted(set(TABLES) - present)}
        day, month, unresolved = conn.execute(
            """SELECT COALESCE(sum(c) FILTER (WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'UTC')
                                                    AT TIME ZONE 'UTC'), 0),
                      COALESCE(sum(c), 0), count(*) FILTER (WHERE state = 'UNRESOLVED')
               FROM (SELECT created_at, state, CASE state WHEN 'RECONCILED' THEN actual_usd
                                                        WHEN 'RELEASED' THEN 0 ELSE reserved_usd END AS c
                     FROM office_budget_reservations
                     WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') r"""
        ).fetchone()
        states = dict(conn.execute("SELECT state, count(*) FROM office_tasks GROUP BY state").fetchall())
        attention = conn.execute(
            """SELECT task_type, state, error_code, attempts, updated_at FROM office_tasks
               WHERE state IN ('BLOCKED','FAILED','WAITING_DATA','WAITING_REVIEW')
               ORDER BY updated_at DESC LIMIT 10""").fetchall()
        events, memos = conn.execute(
            """SELECT (SELECT count(*) FROM office_events WHERE first_observed_at > now() - interval '7 days'),
                      (SELECT count(*) FROM office_memos WHERE created_at > now() - interval '7 days')""").fetchone()
        verdicts = dict(conn.execute(
            """SELECT verdict, count(*) FROM office_reviews WHERE created_at > now() - interval '7 days'
               GROUP BY verdict""").fetchall())
        briefing = conn.execute(
            """SELECT kind, briefing_date, content->'counts', created_at FROM office_briefings
               ORDER BY briefing_date DESC, created_at DESC LIMIT 1""").fetchone()
        mirrored = (external.latest(conn)
                    if conn.execute("SELECT to_regclass('office_external_records') IS NOT NULL").fetchone()[0]
                    else None)
        scored = (qa.latest(conn)
                  if conn.execute("SELECT to_regclass('office_evaluations') IS NOT NULL").fetchone()[0] else None)
    return {**out, "schema": "READY",
            "spend": {"day_usd": str(day), "month_usd": str(month), "unresolved_calls": unresolved},
            "tasks": states,
            "needs_attention": [{"task_type": t, "state": s, "error_code": e, "attempts": a,
                                 "updated_at": u.isoformat()} for t, s, e, a, u in attention],
            "last_7_days": {"events": events, "memos": memos, "reviews": verdicts},
            "mirrored_records": mirrored,
            "qa": scored,
            "latest_briefing": None if briefing is None else {
                "kind": briefing[0], "date": str(briefing[1]), "counts": briefing[2],
                "created_at": briefing[3].isoformat()}}
