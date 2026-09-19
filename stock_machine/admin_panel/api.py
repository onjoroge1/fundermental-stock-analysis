"""Session-authenticated owner UI with one normal login path."""
from __future__ import annotations

import json
import os
from uuid import UUID

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import operations, store
from .security import COOKIE, SESSION_SECONDS, PanelError

router = APIRouter()
ORIGIN = "https://fundermental-stock-analysis.vercel.app"
MAX_BODY = 8192
HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
           "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
           "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"}


def origin_ok(request: Request):
    expected = ORIGIN
    if not os.getenv("VERCEL") and request.url.hostname in {"testserver", "localhost", "127.0.0.1"}:
        expected = str(request.base_url).rstrip("/")
    if str(request.base_url).rstrip("/") != expected or request.headers.get("origin") != expected:
        raise PanelError("ORIGIN_REJECTED", 403)
    if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        raise PanelError("CROSS_SITE_REJECTED", 403)
    if os.getenv("VERCEL") and os.getenv("VERCEL_ENV") != "production":
        raise PanelError("ADMIN_PRODUCTION_ONLY", 403)


async def body(request: Request, required: set[str]):
    origin_ok(request)
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise PanelError("JSON_REQUIRED", 415)
    raw = bytearray()
    try:
        with anyio.fail_after(10):
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > MAX_BODY:
                    raise PanelError("REQUEST_TOO_LARGE", 413)
    except TimeoutError:
        raise PanelError("REQUEST_BODY_TIMEOUT", 408) from None
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise PanelError("INVALID_REQUEST") from None
    if not isinstance(value, dict) or set(value) != required:
        raise PanelError("INVALID_REQUEST")
    return value


def text(value, name, minimum=1, maximum=128):
    result = value[name]
    if not isinstance(result, str) or not minimum <= len(result) <= maximum:
        raise PanelError("INVALID_REQUEST")
    return result


def response(value, status=200, cookie=None, clear=False):
    result = JSONResponse(value, status_code=status, headers=HEADERS)
    if cookie:
        result.set_cookie(COOKIE, cookie, max_age=SESSION_SECONDS, secure=True, httponly=True,
                          samesite="strict", path="/")
    if clear:
        result.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return result


async def safe(fn):
    try:
        return await fn()
    except PanelError as exc:
        return response({"error_code": exc.code}, exc.status)
    except Exception:
        return response({"error_code": "ADMIN_STORAGE_OR_OPERATION_UNAVAILABLE"}, 503)


async def owner(request, *, write=False):
    if write:
        origin_ok(request)
    return await run_in_threadpool(store.session, request.cookies.get(COOKIE, ""),
        csrf=request.headers.get("x-csrf-token"), write=write)


@router.get("/admin")
@router.get("/admin/")
def page():
    from ..config import PROJECT_ROOT
    return FileResponse(PROJECT_ROOT / "webui/admin.html", headers=HEADERS)


@router.get("/api/operator/session")
async def me(request: Request):
    async def work():
        try:
            account = await owner(request)
        except PanelError as exc:
            if exc.status != 401:
                raise
            return response({"authenticated": False})
        return response({"authenticated": True, **account})
    return await safe(work)


@router.post("/api/operator/login")
async def login(request: Request):
    async def work():
        data = await body(request, {"username", "password"})
        raw, account = await run_in_threadpool(store.login, text(data, "username", 1, 64), text(data, "password", 1, 128))
        return response({"authenticated": True, **account}, cookie=raw)
    return await safe(work)


@router.post("/api/operator/logout")
async def logout(request: Request):
    async def work():
        account = await owner(request, write=True)
        await run_in_threadpool(store.logout, request.cookies.get(COOKIE, ""), account["username"])
        return response({"authenticated": False}, clear=True)
    return await safe(work)


@router.post("/api/operator/password")
async def password(request: Request):
    async def work():
        account = await owner(request, write=True)
        data = await body(request, {"current_password", "new_password"})
        raw, updated = await run_in_threadpool(store.change_password, account["username"],
            text(data, "current_password"), text(data, "new_password", 12, 128))
        return response({"authenticated": True, **updated}, cookie=raw)
    return await safe(work)


@router.get("/api/operator/dashboard")
async def dashboard(request: Request):
    async def work():
        await owner(request)
        trading = await run_in_threadpool(operations.trading_summary)
        return response({"controls": await run_in_threadpool(store.controls),
                         "runs": await run_in_threadpool(store.runs),
                         "audit": await run_in_threadpool(store.recent_audit),
                         "connections": await run_in_threadpool(operations.connection_summary),
                         "intelligence_v2": await run_in_threadpool(operations.intelligence_summary),
                         "trading": trading,
                         "trade_execution": trading["mode"]["mode"] == "PAPER",
                         "mode": trading["mode"]["mode"],
                         "broker_submission": False})
    return await safe(work)


@router.post("/api/operator/controls")
async def controls(request: Request):
    async def work():
        account = await owner(request, write=True)
        data = await body(request, {"capture_paused", "expected_version", "reason"})
        if type(data["capture_paused"]) is not bool or type(data["expected_version"]) is not int or data["expected_version"] < 1:
            raise PanelError("INVALID_REQUEST")
        return response(await run_in_threadpool(store.set_capture_pause, account["username"], data["capture_paused"],
            data["expected_version"], text(data, "reason", 3, 300)))
    return await safe(work)


@router.post("/api/operator/trading-mode")
async def trading_mode(request: Request):
    async def work():
        account = await owner(request, write=True)
        data = await body(request, {"mode", "expected_version", "reason"})
        mode = text(data, "mode", 5, 8).upper()
        expected = data["expected_version"]
        if mode not in {"RESEARCH", "PAPER"} or type(expected) is not int or expected < 0:
            raise PanelError("INVALID_REQUEST")
        value = await run_in_threadpool(
            store.set_trading_mode, account["username"], mode,
            None if expected == 0 else expected, text(data, "reason", 3, 300)
        )
        return response(value)
    return await safe(work)


@router.post("/api/operator/runs")
async def start_run(request: Request):
    async def work():
        account = await owner(request, write=True)
        data = await body(request, {"request_id"})
        try:
            identifier = str(UUID(text(data, "request_id", 36, 36)))
        except ValueError:
            raise PanelError("INVALID_RUN_ID") from None
        return response(await run_in_threadpool(store.create_run, account["username"], identifier), 202)
    return await safe(work)


@router.post("/api/operator/runs/{run_id}/step")
async def step(run_id: str, request: Request):
    async def work():
        await owner(request, write=True)
        try:
            identifier = str(UUID(run_id))
        except ValueError:
            raise PanelError("INVALID_RUN_ID") from None
        return response(await run_in_threadpool(operations.run_step, identifier))
    return await safe(work)
