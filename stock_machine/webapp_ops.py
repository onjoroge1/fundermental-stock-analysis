"""Operational API additions layered over the existing dashboard app."""
from __future__ import annotations

import hmac
from datetime import date, timedelta

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import db
from .api_v1_compat import router as api_v1_router
from .backtest.shadow import MODEL_ID
from .backtest.shadow_store import latest
from .market_data import MarketDataUnavailable
from .webapp import app

app.include_router(api_v1_router)


class ControlJobRequest(BaseModel):
    job_type: str
    ticker: str | None = None
    payload: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)
    max_attempts: int = Field(default=3, ge=1, le=5)


def _bearer(authorization: str | None) -> str:
    prefix = "Bearer "
    return authorization[len(prefix):] if authorization and authorization.startswith(prefix) else ""


def _require_admin(authorization: str | None = Header(default=None)) -> None:
    from .control_plane import admin_token
    expected = admin_token()
    if len(expected) < 24:
        raise HTTPException(503, "STOCK_MACHINE_ADMIN_TOKEN is not configured")
    if not hmac.compare_digest(_bearer(authorization), expected):
        raise HTTPException(401, "invalid admin credentials")


def _require_processor(authorization: str | None = Header(default=None)) -> None:
    from .control_plane import admin_token, cron_token
    supplied = _bearer(authorization)
    allowed = [x for x in (admin_token(), cron_token()) if len(x) >= 24]
    if not allowed:
        raise HTTPException(503, "processor auth is not configured")
    if not any(hmac.compare_digest(supplied, expected) for expected in allowed):
        raise HTTPException(401, "invalid processor credentials")


@app.exception_handler(MarketDataUnavailable)
async def market_data_unavailable_handler(
    request: Request, exc: MarketDataUnavailable
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "status": "unavailable", "service": "market_data",
            "reason": str(exc), "retryable": True, "path": request.url.path,
        },
    )


@app.get("/api/alpha-shadow")
def alpha_shadow_status() -> dict:
    conn = db.connect()
    try:
        row = latest(conn, MODEL_ID)
    finally:
        conn.close()
    if row is None:
        return {
            "status": "PENDING", "model_id": MODEL_ID,
            "reason": "no persisted shadow evaluation yet; run scripts/run_shadow_alpha.py",
        }
    result = row["result"]
    return {
        "status": "OK", "run_id": row["run_id"],
        "created_at": row["created_at"],
        "model_id": result.get("model_id", MODEL_ID),
        "coverage": result.get("coverage") or {},
        "model": result.get("model") or {},
        "promotion": result.get("promotion") or {},
        "panel": result.get("panel") or {},
    }


@app.get("/api/p1/{ticker}")
def p1_decision_intelligence(ticker: str) -> dict:
    from .p1 import decision_summary
    return decision_summary(ticker)


@app.get("/api/events/{ticker}")
def company_events(ticker: str, days: int = 370) -> dict:
    from .events.store import current_event_state
    horizon = max(1, min(int(days), 730))
    start = date.today()
    end = start + timedelta(days=horizon)
    conn = db.connect()
    try:
        try:
            result = current_event_state(
                conn, ticker.upper(), start.isoformat(), end.isoformat()
            )
        except Exception as exc:
            conn.rollback()
            return {
                "status": "PENDING", "ticker": ticker.upper(),
                "automation_clear": False,
                "reason": f"event intelligence unavailable: {type(exc).__name__}: {exc}",
            }
    finally:
        conn.close()
    result["status"] = "OK"
    return result


@app.get("/api/events/{ticker}/screen")
def company_event_screen(
    ticker: str, strategy_type: str, front_expiration: str,
    far_expiration: str,
) -> dict:
    from .events.screen import build_event_screen
    conn = db.connect()
    try:
        try:
            return build_event_screen(
                conn, ticker.upper(), strategy_type,
                front_expiration, far_expiration,
            )
        except Exception as exc:
            conn.rollback()
            return {
                "status": "BLOCK", "ticker": ticker.upper(),
                "strategy_type": strategy_type,
                "reasons": [
                    f"event intelligence unavailable: {type(exc).__name__}: {exc}"
                ],
                "warnings": [],
            }
    finally:
        conn.close()


@app.get("/api/strategy-lab-v2")
def strategy_lab_v2_status() -> dict:
    from .strategy_lab_v2_store import latest as latest_strategy_lab
    conn = db.connect()
    try:
        try:
            row = latest_strategy_lab(conn)
        except Exception as exc:
            conn.rollback()
            return {
                "status": "PENDING",
                "reason": f"Strategy Lab v2 storage unavailable: {type(exc).__name__}: {exc}",
            }
    finally:
        conn.close()
    if not row:
        return {
            "status": "PENDING",
            "reason": "no Strategy Lab v2 run exists; enqueue strategy_lab_v2",
        }
    return {
        "status": "OK", "run_id": row["run_id"], "as_of": row["as_of"],
        "panel_hash": row["panel_hash"], "created_at": row["created_at"],
        "result": row["result"],
    }


@app.get("/api/forward-paper-v2")
def forward_paper_v2_status() -> dict:
    from .forward_paper_v2 import list_cohorts, marks, status
    conn = db.connect()
    try:
        try:
            cohorts = list_cohorts(conn)
            rows = []
            for cohort in cohorts:
                cohort_marks = marks(conn, cohort["cohort_id"])
                rows.append({
                    "cohort_id": cohort["cohort_id"],
                    "lab_run_id": cohort["lab_run_id"],
                    "policy_name": cohort["policy_name"],
                    "mode": cohort["mode"],
                    "entry_market_date": cohort["entry_market_date"],
                    "created_at": cohort["created_at"],
                    "longs": cohort["contract"].get("longs") or [],
                    "shorts": cohort["contract"].get("shorts") or [],
                    "control": cohort["contract"].get("control"),
                    "incubation": status(cohort, cohort_marks),
                })
        except Exception as exc:
            conn.rollback()
            return {
                "status": "PENDING",
                "reason": f"Forward Paper v2 storage unavailable: {type(exc).__name__}: {exc}",
            }
    finally:
        conn.close()
    return {
        "status": "OK" if rows else "PENDING",
        "cohort_count": len(rows), "cohorts": rows,
        "creation_policy": "explicit sync only; scheduled jobs may mark but never rebalance/create cohorts",
    }


# ---------- PR32: DB-backed API control plane ----------

@app.get("/api/v1/universe")
def research_universe() -> dict:
    """Return the full universe with sparse indexed research overlaid."""
    from .control_plane import research_index
    from .control_plane_bootstrap import merge_research_universe

    conn = db.connect()
    try:
        companies = db.list_companies(conn)
        try:
            rows = research_index(conn)
        except Exception:
            conn.rollback()
            rows = []
    finally:
        conn.close()
    result = merge_research_universe(companies, rows)
    if result["indexed_count"] == 0:
        result["status"] = "PENDING_INDEX"
        result["reason"] = (
            "research index has not been populated; ticker research endpoints remain available"
        )
    return result


@app.post("/api/admin/jobs")
def create_control_job(
    request: ControlJobRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    from .control_plane import enqueue
    conn = db.connect()
    try:
        return enqueue(
            conn, request.job_type, ticker=request.ticker,
            payload=request.payload, idempotency_key=request.idempotency_key,
            max_attempts=request.max_attempts,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.post("/api/admin/tickers/{ticker}/refresh")
def enqueue_ticker_refresh(
    ticker: str, authorization: str | None = Header(default=None)
) -> dict:
    _require_admin(authorization)
    from .control_plane import enqueue
    conn = db.connect()
    try:
        try:
            return enqueue(conn, "ticker_refresh", ticker=ticker)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.get("/api/admin/jobs")
def control_jobs(
    status: str | None = None, limit: int = 25,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    from .control_plane import list_jobs
    conn = db.connect()
    try:
        rows = list_jobs(conn, status=status, limit=limit)
    finally:
        conn.close()
    return {"status": "OK", "count": len(rows), "jobs": rows}


@app.get("/api/admin/jobs/{job_id}")
def control_job(
    job_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _require_admin(authorization)
    from .control_plane import get_job
    conn = db.connect()
    try:
        row = get_job(conn, job_id)
    finally:
        conn.close()
    if not row:
        raise HTTPException(404, "job not found")
    return row


@app.api_route("/api/admin/process", methods=["GET", "POST"])
def process_control_job(
    authorization: str | None = Header(default=None)
) -> dict:
    """Claim and execute at most one leased job; suitable for manual or Vercel Cron calls."""
    _require_processor(authorization)
    from .control_plane import process_one
    return process_one()


@app.post("/api/admin/migrate")
def migrate_database_to_head(
    authorization: str | None = Header(default=None),
) -> dict:
    """Apply only the checked-in Alembic chain to `head`; no arbitrary target input."""
    _require_admin(authorization)
    from .control_plane_bootstrap import migrate_to_head
    try:
        return migrate_to_head()
    except Exception as exc:
        raise HTTPException(500, f"database migration failed: {type(exc).__name__}: {exc}")
