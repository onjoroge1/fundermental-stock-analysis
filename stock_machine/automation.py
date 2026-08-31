"""Safe production maintenance scheduling for the PR32 control plane.

Automation may refresh data, evaluate Strategy Lab v2, and mark already-frozen
Forward Paper cohorts. It deliberately cannot create/promote Forward Paper
cohorts and cannot place trades.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import db
from .control_plane import ensure_schema, enqueue, research_index


def _indexed_at(row: dict) -> str:
    return str(row.get("indexed_at") or "")


def choose_refresh_ticker(companies: list[dict], indexed_rows: list[dict]) -> str | None:
    """Prefer never-indexed names, then the stalest indexed name."""
    tickers = sorted({str(c.get("ticker") or "").upper() for c in companies if c.get("ticker")})
    if not tickers:
        return None
    by_ticker = {
        str(row.get("ticker") or "").upper(): row
        for row in indexed_rows if row.get("ticker")
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
      * one ticker refresh (unindexed first, then stalest),
      * one Forward Paper mark job when cohorts exist,
      * one Strategy Lab run on Sundays.

    Enqueue idempotency prevents duplicate same-day maintenance jobs.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    scheduled: list[dict] = []

    with db.connect() as conn:
        ensure_schema(conn)
        companies = db.list_companies(conn)
        indexed = research_index(conn)
        ticker = choose_refresh_ticker(companies, indexed)
        if ticker:
            scheduled.append(enqueue(
                conn,
                "ticker_refresh",
                ticker=ticker,
                idempotency_key=f"auto:ticker_refresh:{ticker}:{today}",
            ))

        if _has_forward_cohorts(conn):
            scheduled.append(enqueue(
                conn,
                "forward_paper_mark",
                idempotency_key=f"auto:forward_paper_mark:{today}",
            ))

        # Sunday UTC; this only evaluates policies. It does not freeze cohorts.
        if now.weekday() == 6:
            scheduled.append(enqueue(
                conn,
                "strategy_lab_v2",
                payload={"cost_bps": 15.0, "trigger": "weekly_automation"},
                idempotency_key=f"auto:strategy_lab_v2:{today}",
            ))

    return {
        "status": "OK",
        "scheduled_at": now.isoformat(),
        "scheduled_count": len(scheduled),
        "scheduled": scheduled,
        "safety": {
            "forward_paper_sync_automated": False,
            "trade_execution": False,
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
            cur.execute(
                """SELECT count(*) FROM stock_research_index"""
            )
            indexed_count = int(cur.fetchone()[0])
            cur.execute(
                """SELECT count(*) FROM companies"""
            )
            company_count = int(cur.fetchone()[0])
    return {
        "status": "OK",
        "queue": counts,
        "oldest_pending_at": oldest,
        "research_index": {
            "indexed": indexed_count,
            "companies": company_count,
            "pending": max(0, company_count - indexed_count),
            "coverage_pct": round(indexed_count / company_count * 100.0, 1) if company_count else 0.0,
        },
    }


def cron_tick() -> dict[str, Any]:
    """Schedule due work, then execute at most one queued job."""
    scheduled = schedule_due()
    from .control_plane import process_one
    processed = process_one()
    return {
        "status": "OK",
        "scheduler": scheduled,
        "processor": processed,
    }
