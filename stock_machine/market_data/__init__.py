"""Market-data providers.

Three read-only IBKR paths are available; `get_provider` picks one so callers
(CLI, web API, options engine) do not hard-code a transport:

  tws            TWS / IB Gateway socket API (ibapi) on the local machine.
  client_portal  Client Portal REST gateway.
  remote_bridge  Authenticated HTTPS bridge to TWS / IBGW on another machine.

Select with IBKR_PROVIDER=tws|client_portal|remote_bridge (default: tws).
"""
from __future__ import annotations

import os

from .base import MarketDataProvider
from .models import (
    MarketDataAvailability,
    MarketQuote,
    OptionChainSnapshot,
    OptionContract,
    OptionQuote,
    SessionStatus,
    StrikeSet,
    UnderlyingContract,
)

DEFAULT_PROVIDER = "tws"


class MarketDataUnavailable(RuntimeError):
    """Raised when the configured market-data provider cannot be initialized.

    This is an operational availability condition, not a malformed request.
    Web/API callers can therefore translate it into a structured HTTP 503
    instead of leaking an import/socket error as an Internal Server Error.
    """


def get_provider(name: str | None = None) -> MarketDataProvider:
    """Return a read-only provider. Caller owns closing it.

    Provider import/configuration/connection failures are normalized to
    ``MarketDataUnavailable`` so every caller gets the same explicit failure
    contract. An unknown provider name remains a configuration ``ValueError``.
    """
    choice = (name or os.environ.get("IBKR_PROVIDER", DEFAULT_PROVIDER)).lower()
    allowed = (
        "tws", "gateway", "ibkr_tws",
        "client_portal", "cp", "ibkr_client_portal",
        "remote_bridge", "bridge", "ibkr_bridge",
    )
    if choice not in allowed:
        raise ValueError(
            f"unknown market-data provider {choice!r}; use 'tws', "
            "'client_portal', or 'remote_bridge'"
        )

    try:
        if choice in ("tws", "gateway", "ibkr_tws"):
            from .ibkr_tws import IBKRTWSMarketData

            provider = IBKRTWSMarketData()
            provider.connect()
            return provider

        if choice in ("client_portal", "cp", "ibkr_client_portal"):
            from .ibkr_client_portal import IBKRClientPortalMarketData

            return IBKRClientPortalMarketData()

        from .remote_bridge import RemoteBridgeMarketData

        return RemoteBridgeMarketData()
    except MarketDataUnavailable:
        raise
    except Exception as exc:
        raise MarketDataUnavailable(
            f"{choice} provider unavailable: {type(exc).__name__}: {exc}"
        ) from exc


__all__ = [
    "DEFAULT_PROVIDER",
    "MarketDataAvailability",
    "MarketDataProvider",
    "MarketDataUnavailable",
    "MarketQuote",
    "OptionChainSnapshot",
    "OptionContract",
    "OptionQuote",
    "SessionStatus",
    "StrikeSet",
    "UnderlyingContract",
    "get_provider",
]
