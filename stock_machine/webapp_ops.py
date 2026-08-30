"""Operational API additions layered over the existing dashboard app.

Long-running research jobs remain outside web requests. Endpoints here only
read persisted research state or compute lightweight current-state features.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import Request
from fastapi.responses import JSONResponse

from . import db
from .api_v1_compat import router as api_v1_router
from .backtest.shadow import MODEL_ID
from .backtest.shadow_store import latest
from .market_data import MarketDataUnavailable
from .webapp import app

# Production/Vercel entrypoint mounts the stable read-optimized agent contract.
app.include_router(api_v1_router)


@app.exception_handler(MarketDataUnavailable)
async def market_data_unavailable_handler(
    request: Request, exc: MarketDataUnavailable
) -> JSONResponse:
    """Expose broker/option-provider outages as an explicit service condition."""
    return JSONResponse(
        status_code=503,
        content={
            "status": "unavailable",
            "service": "market_data",
            "reason": str(exc),
            "retryable": True,
            "path": request.url.path,
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
            "status": "PENDING",
            "model_id": MODEL_ID,
            "reason": "no persisted shadow evaluation yet; run scripts/run_shadow_alpha.py",
        }

    result = row["result"]
    return {
        "status": "OK",
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "model_id": result.get("model_id", MODEL_ID),
        "coverage": result.get("coverage") or {},
        "model": result.get("model") or {},
        "promotion": result.get("promotion") or {},
        "panel": result.get("panel") or {},
    }


@app.get("/api/p1/{ticker}")
def p1_decision_intelligence(ticker: str) -> dict:
    """Current P1 decision card plus the latest persisted research verdict."""
    from .p1 import decision_summary

    return decision_summary(ticker)


@app.get("/api/events/{ticker}")
def company_events(ticker: str, days: int = 370) -> dict:
    """Read the latest point-in-time event snapshots and coverage state."""
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
            # Vercel code may deploy before the migration/refresh worker has
            # populated optional event storage. Keep the research API alive,
            # but never describe missing event intelligence as clear.
            return {
                "status": "PENDING",
                "ticker": ticker.upper(),
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
    """Candidate-specific P2-E event/assignment gate for calendars/diagonals."""
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
                "status": "BLOCK",
                "ticker": ticker.upper(),
                "strategy_type": strategy_type,
                "reasons": [
                    f"event intelligence unavailable: {type(exc).__name__}: {exc}"
                ],
                "warnings": [],
            }
    finally:
        conn.close()
