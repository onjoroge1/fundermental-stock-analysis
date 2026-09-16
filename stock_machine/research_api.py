"""Dated operational evidence; reads never contact paid providers or write."""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from . import db, research_store
from .agents.contracts import PILOT
from .automation_api import _require_admin

router = APIRouter(tags=["research-integrity"])


class RunRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9:_.-]+$")


@router.post("/api/admin/research/{ticker}/run")
def run_cycle(ticker: str, body: RunRequest, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    if ticker.upper() not in PILOT:
        raise HTTPException(400, "Ticker is outside the five-name research pilot")
    from .research_cycle import run
    try:
        return run(ticker.upper(), body.idempotency_key)
    except Exception:
        raise HTTPException(503, "Research cycle did not complete; retry with the same key") from None


@router.get("/api/research-operations")
def state():
    try:
        with db.connect() as conn:
            cycles = {t: research_store.latest(conn, "CYCLE_RESULT", t) for t in PILOT}
            provider = {t: research_store.latest(conn, "PROVIDER_DAILY", t) for t in PILOT}
            protocol = research_store.latest(conn, "EXPERIMENT_PROTOCOL")
            forecast = research_store.latest(conn, "EXPERIMENT_FORECAST")
            outcomes = research_store.records(conn, "EXPERIMENT_OUTCOME")
        from .prospective_experiment import verdict
        return {"status": "OK", "research_contract_version": "research-contract.v1",
                "cycles": cycles, "market_observations": provider,
                "experiment": {"protocol": protocol, "latest_forecast": forecast,
                    "evaluation": verdict(protocol["payload"], [r["payload"] for r in outcomes]) if protocol else {"status": "NOT_REGISTERED"}},
                "trade_execution": False}
    except Exception:
        raise HTTPException(503, "Research evidence unavailable; verify migration 0020") from None
