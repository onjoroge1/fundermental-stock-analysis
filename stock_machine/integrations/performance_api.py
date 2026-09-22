"""Reuse owner sessions/CSRF. Reads do not create jobs, artifacts, or tables."""
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool
from ..admin_panel import api as owner_api
from ..admin_panel.security import PanelError
from . import performance_service as service

router = APIRouter()


@router.get("/performance")
def page():
    from ..config import PROJECT_ROOT
    return FileResponse(PROJECT_ROOT / "webui" / "performance.html", headers=owner_api.HEADERS)


@router.get("/api/operator/performance")
async def read(request: Request, ticker: str = "AAPL"):
    async def work():
        await owner_api.owner(request)
        from ..agents.contracts import PILOT
        if ticker not in PILOT:
            raise PanelError("TICKER_OUTSIDE_PILOT", 400)
        return owner_api.response(await run_in_threadpool(service.read_view, ticker))
    return await owner_api.safe(work)


@router.post("/api/operator/performance/reports")
async def report(request: Request):
    async def work():
        await owner_api.owner(request, write=True)
        data = await owner_api.body(request, {"request_id"})
        from uuid import UUID
        try:
            identifier = str(UUID(owner_api.text(data, "request_id", 36, 36)))
        except ValueError:
            raise PanelError("INVALID_RUN_ID", 400) from None
        return owner_api.response(await run_in_threadpool(service.enqueue_report, identifier), 202)
    return await owner_api.safe(work)


@router.get("/api/operator/performance/reports/{job_id}/html")
async def download(job_id: str, request: Request):
    async def work():
        await owner_api.owner(request)
        html = await run_in_threadpool(service.report_html, job_id)
        if html is None:
            raise PanelError("PERFORMANCE_REPORT_NOT_READY", 404)
        return Response(html, media_type="text/html", headers={**owner_api.HEADERS,
                        "Content-Disposition": 'attachment; filename="paper-performance.html"',
                        "Content-Security-Policy": "sandbox; default-src 'none'"})
    return await owner_api.safe(work)


@router.post("/api/operator/performance/jobs/{job_id}/cancel")
async def cancel(job_id: str, request: Request):
    async def work():
        await owner_api.owner(request, write=True)
        await owner_api.body(request, set())
        try:
            value = await run_in_threadpool(service.cancel_report, job_id)
        except ValueError:
            raise PanelError("PERFORMANCE_JOB_NOT_FOUND", 404) from None
        return owner_api.response(value)
    return await owner_api.safe(work)
