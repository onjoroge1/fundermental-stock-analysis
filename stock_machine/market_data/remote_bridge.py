"""Read-only HTTP adapter for a remote stock-machine IBKR bridge.

The cloud application uses this provider when TWS / IB Gateway runs on a
separate trusted machine. The bridge exposes only the canonical market-data
surface; this client has no generic proxy or order/account methods.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .models import (
    MarketQuote,
    OptionChainSnapshot,
    SessionStatus,
    StrikeSet,
    UnderlyingContract,
)

PROVIDER = "remote_bridge"
MAX_CHAIN_STRIKES = 20


class RemoteBridgeError(RuntimeError):
    """A safe failure returned by or while reaching the remote bridge."""


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RemoteBridgeSettings:
    base_url: str
    token: str
    timeout_s: float = 30.0
    verify_ssl: bool = True

    @classmethod
    def from_env(cls) -> "RemoteBridgeSettings":
        return cls(
            base_url=os.environ.get("IBKR_BRIDGE_BASE_URL", "").rstrip("/"),
            token=os.environ.get("IBKR_BRIDGE_TOKEN", ""),
            timeout_s=float(os.environ.get("IBKR_BRIDGE_TIMEOUT_S", "30")),
            verify_ssl=_env_bool("IBKR_BRIDGE_VERIFY_SSL", True),
        )

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("IBKR_BRIDGE_BASE_URL must be an absolute HTTP(S) URL")
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not local:
            raise ValueError("remote IBKR bridge must use HTTPS")
        if not self.verify_ssl and not local:
            raise ValueError("IBKR bridge TLS verification may only be disabled locally")
        if len(self.token) < 24:
            raise ValueError("IBKR_BRIDGE_TOKEN must contain at least 24 characters")
        if self.timeout_s <= 0:
            raise ValueError("IBKR_BRIDGE_TIMEOUT_S must be positive")


class RemoteBridgeMarketData:
    """Canonical MarketDataProvider backed by the authenticated bridge API."""

    def __init__(
        self,
        settings: RemoteBridgeSettings | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or RemoteBridgeSettings.from_env()
        self.settings.validate()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_s,
            verify=self.settings.verify_ssl,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.settings.token}",
                "User-Agent": "stock-machine/0.1 remote-market-data",
            },
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "RemoteBridgeMarketData":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _json(self, method: str, path: str, **kwargs):
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            reason = None
            try:
                payload = exc.response.json()
                if isinstance(payload, dict):
                    reason = payload.get("detail") or payload.get("reason")
            except ValueError:
                pass
            raise RemoteBridgeError(
                f"bridge {method} {path} returned {exc.response.status_code}"
                + (f": {reason}" if reason else "")
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise RemoteBridgeError(
                f"bridge {method} {path} failed: {type(exc).__name__}: {exc}"
            ) from exc

    def session_status(self) -> SessionStatus:
        return SessionStatus.model_validate(self._json("GET", "/v1/session"))

    def resolve_underlying(self, symbol: str) -> UnderlyingContract:
        return UnderlyingContract.model_validate(
            self._json("GET", f"/v1/underlyings/{symbol.upper()}")
        )

    def quote_underlying(self, symbol: str) -> MarketQuote:
        return MarketQuote.model_validate(
            self._json("GET", f"/v1/quotes/{symbol.upper()}")
        )

    def available_expirations(self, symbol: str) -> dict:
        payload = self._json("GET", f"/v1/options/{symbol.upper()}/expirations")
        if not isinstance(payload, dict):
            raise RemoteBridgeError("bridge expiration response must be an object")
        return payload

    def available_strikes(self, symbol: str, month: str) -> StrikeSet:
        return StrikeSet.model_validate(
            self._json(
                "GET",
                f"/v1/options/{symbol.upper()}/strikes",
                params={"month": month.upper()},
            )
        )

    def option_chain(
        self, symbol: str, month: str, strikes: Sequence[float]
    ) -> OptionChainSnapshot:
        wanted = [float(strike) for strike in strikes]
        if not wanted or len(wanted) > MAX_CHAIN_STRIKES:
            raise ValueError(
                f"remote option chain requires 1-{MAX_CHAIN_STRIKES} strikes"
            )
        return OptionChainSnapshot.model_validate(
            self._json(
                "POST",
                f"/v1/options/{symbol.upper()}/chain",
                json={"month": month.upper(), "strikes": wanted},
            )
        )
