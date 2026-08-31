"""Agent/UI route for the persisted trade decision dashboard."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1", tags=["trade-dashboard"])


@router.get("/trade-dashboard")
def trade_dashboard_state() -> dict:
    from .trade_dashboard import build_dashboard
    return build_dashboard()
