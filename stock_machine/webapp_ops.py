"""Operational API additions layered over the existing dashboard app.

Long-running research jobs remain outside web requests. Endpoints here only
read persisted research state or compute lightweight current-state features.
"""
from __future__ import annotations

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
