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


def get_provider(name: str | None = None) -> MarketDataProvider:
    """Return a connected read-only provider. Caller owns closing it."""
    choice = (name or os.environ.get("IBKR_PROVIDER", DEFAULT_PROVIDER)).lower()
    if choice in ("tws", "gateway", "ibkr_tws"):
        from .ibkr_tws import IBKRTWSMarketData

        provider = IBKRTWSMarketData()
        provider.connect()
        return provider
    if choice in ("client_portal", "cp", "ibkr_client_portal"):
        from .ibkr_client_portal import IBKRClientPortalMarketData

        return IBKRClientPortalMarketData()
    raise ValueError(
        f"unknown market-data provider {choice!r}; use 'tws' or 'client_portal'"
    )


__all__ = [
    "DEFAULT_PROVIDER",
    "MarketDataAvailability",
    "MarketDataProvider",
    "MarketQuote",
    "OptionChainSnapshot",
    "OptionContract",
    "OptionQuote",
    "SessionStatus",
    "StrikeSet",
    "UnderlyingContract",
    "get_provider",
]
