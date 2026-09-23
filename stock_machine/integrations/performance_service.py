"""Owner-only read projections and bounded report jobs. No trading writes."""
from __future__ import annotations
from uuid import UUID
from .legacy import PORTFOLIO, export_v1
from .ledger import project


def _job(row):
    return {"job_id": row[0], "status": row[1], "attempts": row[2],
            "created_at": row[3].isoformat(), "updated_at": row[4].isoformat()}


def read_view(ticker: str) -> dict:
    from .. import db, research_store
    from ..agents.contracts import PILOT
    from ..market_calendar import latest_completed_session
    if ticker not in PILOT:
        raise ValueError("TICKER_OUTSIDE_PILOT")
    session = latest_completed_session()
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        exported = export_v1(conn)
        events = exported["events"]
        current = project(events, session) if events else {"status": exported["status"], "positions": []}
        jobs = conn.execute("""SELECT job_id,status,attempts,created_at,updated_at,result
            FROM orchestration_jobs WHERE job_type='performance_report' AND payload->>'portfolio_id'=%s
            ORDER BY created_at DESC LIMIT 10""", (PORTFOLIO,)).fetchall()
        report = next((row[5] for row in jobs if row[1] == "SUCCEEDED" and row[5]), None)
        if report:
            report = {k: v for k, v in report.items() if k != "html"}
        prices = db.fetch_prices(conn, ticker, session)[-180:]
        prices = [{"time": r["date"], "value": float(r["adj_close"])} for r in prices if r.get("adj_close") is not None]
        decisions = conn.execute("""SELECT payload FROM agent_lab_decisions WHERE ticker=%s
            ORDER BY recorded_at DESC LIMIT 50""", (ticker,)).fetchall()
        intelligence = research_store.latest(conn, "AGENT_INTELLIGENCE_V2", ticker)
    rows = []
    for event in events:
        if event.instrument and event.instrument.symbol == ticker and event.kind == "FILL":
            rows.append({"event_id": event.event_id, "kind": "LEGACY_SIMULATED_FILL", "ticker": ticker,
                         "occurred_at": event.occurred_at.isoformat(), "recorded_at": event.recorded_at.isoformat(),
                         "chart_day": event.occurred_at.date().isoformat(), "price": str(event.price),
                         "quantity": str(event.quantity), "decision_id": event.decision_id, "rationale": "Legacy adjusted-close simulation, not prospective evidence."})
    for row in decisions:
        d = row[0]
        rows.append({"event_id": d["decision_id"], "decision_id": d["decision_id"], "kind": "RESEARCH_DECISION",
                     "ticker": ticker, "occurred_at": d.get("decided_at"), "recorded_at": d.get("observed_at"),
                     "chart_day": (d.get("decided_at") or "")[:10], "price_date_basis": d.get("price_date"),
                     "status": d.get("status"), "rationale": d.get("rationale"), "blockers": d.get("blockers") or []})
    rows.sort(key=lambda r: (r.get("occurred_at") or "", r["event_id"]), reverse=True)
    v2 = (intelligence or {}).get("payload") or {}
    return {"schema_version": "owner-performance.v1", "portfolio_id": PORTFOLIO,
            "quality": "LEGACY_SIMULATION", "session": session, "current": current,
            "jobs": [_job(r) for r in jobs], "latest_report": report,
            "replay": {"ticker": ticker, "price_basis": "ADJUSTED_CLOSE", "prices": prices, "events": rows[:200]},
            "intelligence": {"mode": v2.get("mode"), "decision_id": v2.get("decision_id"),
                             "state": {k: (v2.get("state") or {}).get(k) for k in ("direction", "bias_score", "blockers")},
                             "selected": v2.get("selected"), "status": "RECORDED" if intelligence else "NOT_RUN"},
            "limitations": exported.get("limitations") or [], "broker_submission": False}


def enqueue_report(request_id: str) -> dict:
    from .. import db, control_plane as cp
    from .reporting import freeze_input
    request_id = str(UUID(request_id))
    key = "performance:"+request_id
    with db.connect() as conn:
        row = conn.execute("""SELECT job_id,status,attempts,created_at,updated_at FROM orchestration_jobs
            WHERE idempotency_key=%s AND job_type='performance_report'""", (key,)).fetchone()
        if row:
            return _job(row)
    with db.connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        payload = freeze_input(conn, PORTFOLIO)
    with db.connect() as conn:
        job = cp.enqueue(conn, "performance_report", payload=payload, idempotency_key=key)
    return {k: job.get(k) for k in ("job_id", "status", "attempts", "created_at", "updated_at")}


def report_html(job_id: str) -> str | None:
    from .. import db
    with db.connect() as conn:
        row = conn.execute("""SELECT result FROM orchestration_jobs WHERE job_id=%s
            AND job_type='performance_report' AND status='SUCCEEDED' AND payload->>'portfolio_id'=%s""",
            (job_id, PORTFOLIO)).fetchone()
    return (row[0] or {}).get("html") if row else None


def cancel_report(job_id: str) -> dict:
    from .. import db
    from .workers import cancel
    with db.connect() as conn:
        row = conn.execute("SELECT job_id FROM orchestration_jobs WHERE job_id=%s AND job_type='performance_report' AND payload->>'portfolio_id'=%s",
                           (job_id, PORTFOLIO)).fetchone()
        if not row:
            raise ValueError("PERFORMANCE_JOB_NOT_FOUND")
        return cancel(conn, job_id)
