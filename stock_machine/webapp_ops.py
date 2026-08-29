"""Operational API additions layered over the existing dashboard app.

Keeping this module thin avoids coupling long-running research jobs to web
requests. The endpoint below is read-only and only exposes the latest persisted
shadow run created by scripts/run_shadow_alpha.py.
"""
from __future__ import annotations

from . import db
from .backtest.shadow import MODEL_ID
from .backtest.shadow_store import latest
from .webapp import app


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
