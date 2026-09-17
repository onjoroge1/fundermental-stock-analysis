"""Reads are inert. Writes are admin-authenticated research with DB controls."""
from __future__ import annotations

import hmac
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse

from . import journal
from .contracts import CaptureRequest, PILOT, ReviewRequest

router = APIRouter(tags=["agent-lab"])


def configured_admin_token():
    from ..control_plane import admin_token
    return admin_token()


def panel_capture_paused():
    from ..admin_panel.store import controls
    try:
        return controls()["capture_paused"]
    except Exception:
        raise HTTPException(503, "Capture controls unavailable; verify migration 0022") from None


def require_capture(authorization: str | None = Header(default=None)):
    expected = configured_admin_token()
    if len(expected) < 24:
        raise HTTPException(503, "Admin authentication is not configured")
    supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Invalid admin credentials")
    if panel_capture_paused():
        raise HTTPException(409, "Capture is paused by an administrator")


def _read(call):
    try:
        return call()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(503, "Agent journal unavailable; verify database and migration 0022") from None


@router.get("/agents")
@router.get("/agents/{ticker}")
def page(ticker: str | None = None):
    if ticker and ticker.upper() not in PILOT:
        raise HTTPException(404, "Ticker is outside the research pilot")
    from ..config import PROJECT_ROOT
    return FileResponse(PROJECT_ROOT / "webui" / "agents.html")


@router.get("/api/agent-lab")
def state(ticker: str | None = None, status: Literal["RECORDED", "BLOCKED", "FAILED"] | None = None,
          limit: int = Query(50, ge=1, le=100), day: date | None = None):
    data = _read(lambda: journal.dashboard(ticker.upper() if ticker else None, status, limit, day))
    data["capture_enabled"] = not panel_capture_paused()
    return data


@router.get("/api/agent-lab/decisions/{decision_id}")
def decision_detail(decision_id: UUID):
    result = _read(lambda: journal.detail(str(decision_id)))
    if result is None:
        raise HTTPException(404, "Decision not found")
    return result


@router.post("/api/admin/agents/{ticker}/capture", dependencies=[Depends(require_capture)])
def capture(ticker: str, body: CaptureRequest):
    try:
        return journal.capture(ticker, body)
    except journal.JournalConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(503, "Capture did not commit; retry with the same idempotency key") from None


@router.post("/api/admin/agent-decisions/{decision_id}/reviews", dependencies=[Depends(require_capture)])
def review(decision_id: UUID, body: ReviewRequest):
    try:
        return journal.append_review(str(decision_id), body)
    except KeyError:
        raise HTTPException(404, "Decision not found") from None
    except journal.JournalConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except Exception:
        raise HTTPException(503, "Review did not commit; retry with the same idempotency key") from None
