"""Safe production maintenance scheduling for the PR32 control plane.

Automation may refresh data, evaluate Strategy Lab v2, mark already-frozen
Forward Paper cohorts, and advance covered-stock research through the immutable Agent
Lab journal. When the owner has selected PAPER mode, a completed covered-stock decision
may create a deterministic simulated Agent Trading v1 intent/fill. Automation
still cannot create/promote Forward Paper cohorts or submit broker orders.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import db
from .control_plane import ensure_schema, enqueue, research_index
from .market_calendar import latest_completed_session

MAX_JOBS_PER_CRON_TICK = 2


def choose_agent_ticker(conn, session: str) -> str | None:
    """Drain the whole ready universe once per session, including UTC rollover."""
    from .agents.contracts import AGENT_UNIVERSE

    row = conn.execute(
        """SELECT c.ticker FROM companies c
        JOIN LATERAL (SELECT max(date)::text AS market_date FROM prices_daily
            WHERE ticker=c.ticker AND date<=%s) p ON p.market_date=%s
        JOIN LATERAL (SELECT max_record_date::text AS market_date,status
            FROM dataset_snapshots WHERE ticker=c.ticker AND dataset='prices'
            ORDER BY last_checked_at DESC,observed_at DESC LIMIT 1) s
            ON s.market_date=%s AND s.status='PASS'
        WHERE c.ticker=ANY(%s) AND NOT EXISTS (
            SELECT 1 FROM orchestration_jobs j WHERE
                j.idempotency_key='auto:research_cycle:' || c.ticker || ':' || %s)
        ORDER BY c.ticker LIMIT 1""",
        (session, session, session, list(AGENT_UNIVERSE), session),
    ).fetchone()
    return row[0] if row else None


def _indexed_at(row: dict) -> str:
    return str(row.get("indexed_at") or "")


def choose_refresh_ticker(
    companies: list[dict], indexed_rows: list[dict]
) -> str | None:
    """Prefer never-indexed names, then the stalest indexed name."""
    tickers = sorted(
        {str(c.get("ticker") or "").upper() for c in companies if c.get("ticker")}
    )
    if not tickers:
        return None
    by_ticker = {
        str(row.get("ticker") or "").upper(): row
        for row in indexed_rows
        if row.get("ticker")
    }
    missing = [ticker for ticker in tickers if ticker not in by_ticker]
    if missing:
        return missing[0]
    return min(tickers, key=lambda ticker: (_indexed_at(by_ticker[ticker]), ticker))


def _has_forward_cohorts(conn) -> bool:
    try:
        from .forward_paper_v2 import list_cohorts

        return bool(list_cohorts(conn))
    except Exception:
        conn.rollback()
        return False


def schedule_due(now: datetime | None = None) -> dict[str, Any]:
    """Enqueue bounded due work without executing it.

    Every call schedules at most:
      * one covered-stock agent cycle per call after that ticker is current for the
        latest completed market session (all 54 covered names/session),
      * one index refresh (unindexed first, then stalest),
      * one Forward Paper mark job when cohorts exist,
      * one Strategy Lab run on Sundays,
      * one hourly matured Agent Intelligence outcome scan late in UTC day.

    Enqueue idempotency prevents duplicate same-day maintenance jobs.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    scheduled: list[dict] = []

    with db.connect() as conn:
        ensure_schema(conn)
        # Concurrent cron deliveries must select different due names rather
        # than both consuming the same one-name scheduling slot.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('agent-universe-scheduler',0))"
        )
        # One bounded agent cycle per ticker and completed market session.
        # Do not consume the session key before the selected ticker's post-close
        # price refresh has landed. Weekend/holiday UTC rollovers keep the same
        # exchange-session key, so Friday research can finish after Friday's
        # post-close refresh instead of becoming stranded on Saturday UTC.
        session = latest_completed_session(now)
        from .admin_panel.store import controls

        if controls(conn)["capture_enabled"]:
            ticker = choose_agent_ticker(conn, session)
            if ticker:
                scheduled.append(
                    enqueue(
                        conn,
                        "research_cycle",
                        ticker=ticker,
                        idempotency_key=f"auto:research_cycle:{ticker}:{session}",
                    )
                )
        companies = db.list_companies(conn)
        indexed = research_index(conn)
        ticker = choose_refresh_ticker(companies, indexed)
        if ticker:
            scheduled.append(
                enqueue(
                    conn,
                    "research_index_refresh",
                    ticker=ticker,
                    idempotency_key=f"auto:research_index_refresh:{ticker}:{today}",
                )
            )

        if _has_forward_cohorts(conn):
            scheduled.append(
                enqueue(
                    conn,
                    "forward_paper_mark",
                    idempotency_key=f"auto:forward_paper_mark:{today}",
                )
            )

        # Score matured v2 outcomes once daily. Repeated late-day cron
        # deliveries reuse the same idempotency key.
        if now.hour >= 22:
            scheduled.append(
                enqueue(
                    conn,
                    "agent_intelligence_outcomes",
                    payload={"limit": 100},
                    idempotency_key=f"auto:agent_intelligence_outcomes:{today}:{now.hour}",
                )
            )

        # Sunday UTC; this only evaluates policies. It does not freeze cohorts.
        if now.weekday() == 6:
            scheduled.append(
                enqueue(
                    conn,
                    "strategy_lab_v2",
                    payload={"cost_bps": 15.0, "trigger": "weekly_automation"},
                    idempotency_key=f"auto:strategy_lab_v2:{today}",
                )
            )

    return {
        "status": "OK",
        "scheduled_at": now.isoformat(),
        "scheduled_count": len(scheduled),
        "scheduled": scheduled,
        "safety": {
            "forward_paper_sync_automated": False,
            "paper_simulation_possible": True,
            "broker_submission": False,
            "live_trade_execution": False,
        },
    }


def queue_health() -> dict[str, Any]:
    with db.connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT status, count(*) FROM orchestration_jobs GROUP BY status"""
            )
            counts = {str(status): int(count) for status, count in cur.fetchall()}
            cur.execute(
                """SELECT min(created_at) FROM orchestration_jobs WHERE status='PENDING'"""
            )
            row = cur.fetchone()
            oldest = row[0].isoformat() if row and row[0] else None
            cur.execute("""SELECT count(*) FROM stock_research_index""")
            indexed_count = int(cur.fetchone()[0])
            cur.execute("""SELECT count(*) FROM companies""")
            company_count = int(cur.fetchone()[0])
    return {
        "status": "OK",
        "queue": counts,
        "oldest_pending_at": oldest,
        "research_index": {
            "indexed": indexed_count,
            "companies": company_count,
            "pending": max(0, company_count - indexed_count),
            "coverage_pct": (
                round(indexed_count / company_count * 100.0, 1)
                if company_count
                else 0.0
            ),
        },
    }


def cron_tick() -> dict[str, Any]:
    """Schedule due work, then drain a small bounded number of queued jobs.

    Normal scheduling can create both a pilot agent cycle and a source/index
    refresh. Processing only one job per hourly tick lets the queue grow even
    when every job is healthy. Two jobs per ten-minute tick keep expanded-universe throughput balanced while
    preserving a hard serverless work bound.
    """
    # Frozen instructions precede fresh decisions. Bound DB-only fills separately
    # from the two provider jobs so expanded coverage does not imply a long loop.
    from . import agent_trading
    from .admin_panel.store import controls

    paper = {"status": "SKIPPED"}
    if controls()["capture_enabled"] and agent_trading.get_mode()["mode"] == "PAPER":
        pending = agent_trading.process_pending(limit=6)
        exits = agent_trading.settle_holding_limits(limit=6)
        try:
            mark = agent_trading.mark_open_positions()
        except ValueError as exc:
            mark = {"status": "BLOCKED", "reason": str(exc)}
        paper = {"pending": pending, "holding_limits": exits, "mark": mark}
    scheduled = schedule_due()
    from .control_plane import process_one

    processed = []
    idle = None
    for _ in range(MAX_JOBS_PER_CRON_TICK):
        result = process_one()
        if result.get("status") == "IDLE":
            idle = result
            break
        processed.append(result)
    primary = (
        processed[0]
        if processed
        else (idle or {"status": "IDLE", "message": "no runnable jobs"})
    )
    return {
        "status": "OK",
        "scheduler": scheduled,
        "processor": primary,
        "processors": processed,
        "processed_count": len(processed),
        "max_jobs_per_tick": MAX_JOBS_PER_CRON_TICK,
        "paper_maintenance": paper,
    }
