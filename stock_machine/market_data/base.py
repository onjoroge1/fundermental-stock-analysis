"""Provider boundary used by the future options engine."""
from __future__ import annotations

from typing import Protocol, Sequence

from .models import (
    MarketQuote,
    OptionChainSnapshot,
    SessionStatus,
    StrikeSet,
    UnderlyingContract,
)


class MarketDataProvider(Protocol):
    """Read-only interface; deliberately contains no order methods."""

    def session_status(self) -> SessionStatus: ...

    def resolve_underlying(self, symbol: str) -> UnderlyingContract: ...

    def quote_underlying(self, symbol: str) -> MarketQuote: ...

    def available_strikes(self, symbol: str, month: str) -> StrikeSet: ...

    def option_chain(
        self, symbol: str, month: str, strikes: Sequence[float]
    ) -> OptionChainSnapshot: ...
