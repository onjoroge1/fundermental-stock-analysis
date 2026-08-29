"""Minimal authenticated HTTP bridge from cloud callers to local TWS/IBGW.

Run this process on the same Mac/host as TWS or IB Gateway, then publish only
this HTTP service through a secure tunnel. The API is read-only by
construction: it exposes quotes, contracts, expirations, strikes and option
chains, and contains no account or order endpoints.
"""
from __future__ import annotations

import hmac
import os
import re
import threading
from collections.abc import Callable

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from .market_data import MarketDataUnavailable, get_provider

SERVICE = "stock-machine-ibkr-bridge"
_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,20}$")
_MONTH_RE = re.compile(r"^[A-Z]{3}\d{2}$")
_PROVIDER_LOCK = threading.Lock()

app = FastAPI(
    title="Stock Machine IBKR Market Data Bridge",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class ChainRequest(BaseModel):
    month: str = Field(pattern=r"^[A-Z]{3}\d{2}$")
    strikes: list[float] = Field(min_length=1, max_length=20)

    @field_validator("month")
    @classmethod
    def normalize_month(cls, value: str) -> str:
        return value.upper()

    @field_validator("strikes")
    @classmethod
    def validate_strikes(cls, values: list[float]) -> list[float]:
        if any(value <= 0 for value in values):
            raise ValueError("strikes must be positive")
        return values


def _required_token() -> str:
    token = os.environ.get("IBKR_BRIDGE_TOKEN", "")
    if len(token) < 24:
        raise RuntimeError(
            "IBKR_BRIDGE_TOKEN is required and must contain at least 24 characters"
        )
    return token


def _authorize(authorization: str | None = Header(default=None)) -> None:
    expected = _required_token()
    prefix = "Bearer "
    supplied = (
        authorization[len(prefix):]
        if authorization and authorization.startswith(prefix)
        else ""
    )
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="invalid bridge credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _symbol(value: str) -> str:
    normalized = value.strip().upper()
    if not _SYMBOL_RE.fullmatch(normalized):
        raise HTTPException(400, "invalid ticker symbol")
    return normalized


def _month(value: str) -> str:
    normalized = value.strip().upper()
    if not _MONTH_RE.fullmatch(normalized):
        raise HTTPException(400, "option month must use IBKR format such as AUG26")
    return normalized


def _with_tws(operation: Callable):
    """Serialize bridge requests onto one TWS client-id namespace.

    Each request gets a fresh socket connection and closes it deterministically.
    Serialization avoids concurrent connections colliding on the configured
    IBKR_TWS_CLIENT_ID and keeps the first bridge implementation predictable.
    """
    with _PROVIDER_LOCK:
        provider = None
        try:
            provider = get_provider("tws")
            return operation(provider)
        except HTTPException:
            raise
        except MarketDataUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                503, f"IBKR market data unavailable: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            if provider is not None:
                try:
                    provider.close()
                except Exception:
                    pass


@app.middleware("http")
async def no_store(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/healthz")
def healthz(response: Response) -> dict:
    """Unauthenticated process health only; it does not probe or expose IBKR."""
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok", "service": SERVICE, "read_only": True}


@app.get("/v1/session", dependencies=[Depends(_authorize)])
def session_status() -> dict:
    return _with_tws(lambda provider: provider.session_status().model_dump(mode="json"))


@app.get("/v1/underlyings/{symbol}", dependencies=[Depends(_authorize)])
def resolve_underlying(symbol: str) -> dict:
    ticker = _symbol(symbol)
    return _with_tws(
        lambda provider: provider.resolve_underlying(ticker).model_dump(mode="json")
    )


@app.get("/v1/quotes/{symbol}", dependencies=[Depends(_authorize)])
def quote_underlying(symbol: str) -> dict:
    ticker = _symbol(symbol)
    return _with_tws(
        lambda provider: provider.quote_underlying(ticker).model_dump(mode="json")
    )


@app.get(
    "/v1/options/{symbol}/expirations",
    dependencies=[Depends(_authorize)],
)
def option_expirations(symbol: str) -> dict:
    ticker = _symbol(symbol)

    def load(provider):
        if not hasattr(provider, "available_expirations"):
            raise HTTPException(501, "TWS provider exposes no expiration list")
        return provider.available_expirations(ticker)

    return _with_tws(load)


@app.get("/v1/options/{symbol}/strikes", dependencies=[Depends(_authorize)])
def option_strikes(symbol: str, month: str) -> dict:
    ticker = _symbol(symbol)
    normalized_month = _month(month)
    return _with_tws(
        lambda provider: provider.available_strikes(
            ticker, normalized_month
        ).model_dump(mode="json")
    )


@app.post("/v1/options/{symbol}/chain", dependencies=[Depends(_authorize)])
def option_chain(symbol: str, request: ChainRequest) -> dict:
    ticker = _symbol(symbol)
    return _with_tws(
        lambda provider: provider.option_chain(
            ticker, request.month, request.strikes
        ).model_dump(mode="json")
    )


def main() -> None:
    """CLI entrypoint; keep the origin bound to loopback for tunnel-only access."""
    host = os.environ.get("IBKR_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("IBKR_BRIDGE_PORT", "8765"))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "IBKR bridge must bind to loopback; publish it through a secure tunnel"
        )
    _required_token()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
