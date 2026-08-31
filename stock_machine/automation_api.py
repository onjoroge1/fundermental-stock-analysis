"""Authenticated PR34 maintenance endpoints."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException

from .control_plane import admin_token, cron_token

router = APIRouter(prefix="/api/admin", tags=["control-plane-automation"])


def _bearer(value: str | None) -> str:
    prefix = "Bearer "
    return value[len(prefix):] if value and value.startswith(prefix) else ""


def _require_admin(value: str | None) -> None:
    expected = admin_token()
    if len(expected) < 24:
        raise HTTPException(503, "STOCK_MACHINE_ADMIN_TOKEN is not configured")
    if not hmac.compare_digest(_bearer(value), expected):
        raise HTTPException(401, "invalid admin credentials")


def _require_processor(value: str | None) -> None:
    supplied = _bearer(value)
    allowed = [secret for secret in (admin_token(), cron_token()) if len(secret) >= 24]
    if not allowed:
        raise HTTPException(503, "processor auth is not configured")
    if not any(hmac.compare_digest(supplied, secret) for secret in allowed):
        raise HTTPException(401, "invalid processor credentials")


@router.get("/automation/health")
def automation_health(
    authorization: str | None = Header(default=None),
) -> dict:
    _require_admin(authorization)
    from .automation import queue_health
    return queue_health()


@router.post("/automation/schedule")
def automation_schedule(
    authorization: str | None = Header(default=None),
) -> dict:
    """Admin-only dry scheduling step: enqueue due work, execute nothing."""
    _require_admin(authorization)
    from .automation import schedule_due
    return schedule_due()


@router.get("/cron")
def automation_cron(
    authorization: str | None = Header(default=None),
) -> dict:
    """Vercel Cron entrypoint: schedule bounded due work and process one job."""
    _require_processor(authorization)
    from .automation import cron_tick
    return cron_tick()
