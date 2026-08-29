"""Market-data providers.

Two read-only IBKR paths are available; `get_provider` picks one so callers
(CLI, web API, options engine) do not hard-code a vendor:

  tws            TWS / IB Gateway socket API (ibapi). No web gateway needed;
                 serves delayed data without a subscription.
  client_portal  Client Portal REST gateway.

Select with IBKR_PROVIDER=tws|client_portal (default: tws).
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
    """Return a connected read-only provider. Caller owns closing it.

    Provider import, configuration and connection failures are normalized to
    ``MarketDataUnavailable`` so every caller gets the same explicit failure
    contract. An unknown provider name remains a configuration ``ValueError``.
    """
    choice = (name or os.environ.get("IBKR_PROVIDER", DEFAULT_PROVIDER)).lower()
    if choice not in (
        "tws", "gateway", "ibkr_tws",
        "client_portal", "cp", "ibkr_client_portal",
    ):
        raise ValueError(
            f"unknown market-data provider {choice!r}; use 'tws' or 'client_portal'"
        )

    try:
        if choice in ("tws", "gateway", "ibkr_tws"):
            from .ibkr_tws import IBKRTWSMarketData

            provider = IBKRTWSMarketData()
            provider.connect()
            return provider

        from .ibkr_client_portal import IBKRClientPortalMarketData

        return IBKRClientPortalMarketData()
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
