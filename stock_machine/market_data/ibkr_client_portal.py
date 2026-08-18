"""Read-only Interactive Brokers Client Portal market-data adapter.

This module intentionally exposes contract discovery and market snapshots
only. It contains no account order URL, order model, or generic public request
method that a caller could use to place trades.
"""
from __future__ import annotations

import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import httpx

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

PROVIDER = "ibkr_client_portal"
SNAPSHOT_FIELDS = (
    "31", "55", "84", "85", "86", "87", "88", "6509", "7059",
    "7308", "7309", "7310", "7311", "7633", "7635", "7638",
)
MAX_CHAIN_STRIKES = 20
MAX_SNAPSHOT_CONIDS = 50


class IBKRMarketDataError(RuntimeError):
    """A safe, provider-level market-data failure."""


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class IBKRClientPortalSettings:
    base_url: str = "https://localhost:5000/v1/api"
    verify_ssl: bool = False
    timeout_s: float = 20.0
    snapshot_wait_s: float = 0.25
    min_request_interval_s: float = 0.11

    @classmethod
    def from_env(cls) -> "IBKRClientPortalSettings":
        return cls(
            base_url=os.environ.get(
                "IBKR_CP_BASE_URL", "https://localhost:5000/v1/api"
            ).rstrip("/"),
            verify_ssl=_env_bool("IBKR_CP_VERIFY_SSL", False),
            timeout_s=float(os.environ.get("IBKR_CP_TIMEOUT_S", "20")),
            snapshot_wait_s=float(
                os.environ.get("IBKR_CP_SNAPSHOT_WAIT_S", "0.25")
            ),
            min_request_interval_s=float(
                os.environ.get("IBKR_CP_MIN_REQUEST_INTERVAL_S", "0.11")
            ),
        )

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("IBKR_CP_BASE_URL must be an absolute HTTP(S) URL")
        if not self.verify_ssl and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError(
                "SSL verification may only be disabled for the local IBKR gateway"
            )
        if (self.timeout_s <= 0 or self.snapshot_wait_s < 0
                or self.min_request_interval_s < 0):
            raise ValueError(
                "IBKR timeout must be positive and delays must be nonnegative"
            )


def _rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("secdef", "contracts", "data"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
        if payload.get("conid") is not None:
            return [payload]
    return []


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "--", "N/A"}:
        return None
    text = re.sub(r"^[CH]", "", text).rstrip("%")
    multiplier = 1.0
    if text.endswith("K"):
        multiplier, text = 1_000.0, text[:-1]
    elif text.endswith("M"):
        multiplier, text = 1_000_000.0, text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _date(value) -> date:
    text = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise IBKRMarketDataError(f"unrecognized IBKR expiration date {value!r}")


def _availability(code: str | None) -> MarketDataAvailability:
    first = (code or "")[:1]
    return {
        "R": MarketDataAvailability.REALTIME,
        "D": MarketDataAvailability.DELAYED,
        "Z": MarketDataAvailability.FROZEN,
        "Y": MarketDataAvailability.FROZEN_DELAYED,
        "N": MarketDataAvailability.NOT_SUBSCRIBED,
        "i": MarketDataAvailability.INCOMPLETE,
    }.get(first, MarketDataAvailability.UNKNOWN)


def _month(value: str) -> str:
    normalized = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}\d{2}", normalized):
        raise ValueError("option month must use IBKR format such as AUG26")
    return normalized


class IBKRClientPortalMarketData:
    """Synchronous, read-only Client Portal provider."""

    def __init__(
        self,
        settings: IBKRClientPortalSettings | None = None,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or IBKRClientPortalSettings.from_env()
        self.settings.validate()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.settings.base_url,
            verify=self.settings.verify_ssl,
            timeout=self.settings.timeout_s,
            headers={
                "Accept": "application/json",
                "User-Agent": "stock-machine/0.1 read-only-market-data",
                "Host": "api.ibkr.com",
            },
        )
        self._sleep = sleeper
        self._accounts_loaded = False
        self._snapshot_primed = False
        self._last_request_at = 0.0

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "IBKRClientPortalMarketData":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _json(self, method: str, path: str, **kwargs):
        try:
            elapsed = time.monotonic() - self._last_request_at
            delay = self.settings.min_request_interval_s - elapsed
            if delay > 0:
                self._sleep(delay)
            response = self._client.request(method, path, **kwargs)
            self._last_request_at = time.monotonic()
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IBKRMarketDataError(
                f"IBKR {method} {path} failed: {exc}"
            ) from exc

    def session_status(self) -> SessionStatus:
        payload = self._json("POST", "/iserver/auth/status", json={})
        return SessionStatus(
            provider=PROVIDER,
            connected=bool(payload.get("connected")),
            authenticated=bool(payload.get("authenticated")),
            competing=bool(payload.get("competing")),
            message=str(payload.get("message") or ""),
        )

    def _load_accounts(self) -> None:
        if not self._accounts_loaded:
            self._json("GET", "/iserver/accounts")
            self._accounts_loaded = True

    def resolve_underlying(self, symbol: str) -> UnderlyingContract:
        symbol = symbol.strip().upper()
        payload = self._json(
            "GET",
            "/iserver/secdef/search",
            params={"symbol": symbol, "secType": "STK"},
        )
        candidates = _rows(payload)
        exact = [
            row for row in candidates
            if str(row.get("symbol") or row.get("ticker") or "").upper()
            == symbol
        ]
        if not exact:
            raise IBKRMarketDataError(f"no exact IBKR stock contract for {symbol}")

        row = next(
            (candidate for candidate in exact
             if str(candidate.get("assetClass") or "STK").upper() == "STK"),
            exact[0],
        )
        sections = row.get("sections") if isinstance(row.get("sections"), list) else []
        option_sections = [
            section for section in sections
            if str(section.get("secType") or "").upper() == "OPT"
        ]
        months: set[str] = set()
        for section in option_sections:
            raw_months = section.get("months") or ""
            months.update(
                part.strip().upper()
                for part in re.split(r"[;,]", str(raw_months))
                if part.strip()
            )
        try:
            conid = int(row["conid"])
        except (KeyError, TypeError, ValueError) as exc:
            raise IBKRMarketDataError(
                f"IBKR contract for {symbol} has no valid conid"
            ) from exc
        return UnderlyingContract(
            provider=PROVIDER,
            symbol=symbol,
            conid=conid,
            name=row.get("companyName") or row.get("name"),
            currency=str(row.get("currency") or "USD"),
            exchange=(row.get("listingExchange") or row.get("exchange")),
            has_options=bool(row.get("hasOptions") or option_sections),
            option_months=sorted(months),
        )

    def available_strikes(self, symbol: str, month: str) -> StrikeSet:
        month = _month(month)
        underlying = self.resolve_underlying(symbol)
        payload = self._json(
            "GET",
            "/iserver/secdef/strikes",
            params={"conid": underlying.conid, "sectype": "OPT", "month": month},
        )

        def numbers(key: str) -> list[float]:
            values = payload.get(key) or payload.get(key.capitalize()) or []
            parsed = [_number(value) for value in values]
            return sorted({value for value in parsed if value and value > 0})

        return StrikeSet(
            provider=PROVIDER,
            underlying=underlying,
            month=month,
            call_strikes=numbers("call"),
            put_strikes=numbers("put"),
        )

    def _option_contract(
        self,
        underlying: UnderlyingContract,
        month: str,
        strike: float,
        right: str,
    ) -> OptionContract:
        payload = self._json(
            "GET",
            "/iserver/secdef/info",
            params={
                "conid": underlying.conid,
                "sectype": "OPT",
                "month": month,
                "strike": strike,
                "right": right,
                "exchange": "SMART",
            },
        )
        rows = _rows(payload)
        if not rows:
            raise IBKRMarketDataError(
                f"no IBKR contract for {underlying.symbol} {month} "
                f"{strike:g}{right}"
            )
        row = rows[0]
        expiry = (
            row.get("maturityDate")
            or row.get("expiry")
            or row.get("lastTradingDay")
        )
        contract_right = str(
            row.get("right") or row.get("putOrCall") or right
        ).upper()[:1]
        return OptionContract(
            provider=PROVIDER,
            conid=int(row["conid"]),
            symbol=underlying.symbol,
            underlying_conid=underlying.conid,
            expiration=_date(expiry),
            strike=float(row.get("strike") or strike),
            right=contract_right,
            multiplier=int(float(row.get("multiplier") or 100)),
            currency=str(row.get("currency") or underlying.currency),
            exchange=str(row.get("exchange") or "SMART"),
            description=(row.get("description") or row.get("contractDesc")),
        )

    def _snapshot(self, conids: Sequence[int]) -> dict[int, dict]:
        unique = list(dict.fromkeys(int(conid) for conid in conids))
        if not unique or len(unique) > MAX_SNAPSHOT_CONIDS:
            raise ValueError(
                f"snapshot requires 1-{MAX_SNAPSHOT_CONIDS} unique conids"
            )
        self._load_accounts()
        params = {
            "conids": ",".join(str(conid) for conid in unique),
            "fields": ",".join(SNAPSHOT_FIELDS),
        }
        payload = self._json("GET", "/iserver/marketdata/snapshot", params=params)
        if not self._snapshot_primed:
            self._snapshot_primed = True
            self._sleep(self.settings.snapshot_wait_s)
            payload = self._json(
                "GET", "/iserver/marketdata/snapshot", params=params
            )
        return {
            int(row["conid"]): row
            for row in _rows(payload)
            if row.get("conid") is not None
        }

    @staticmethod
    def _quote(row: dict, *, symbol: str | None = None) -> MarketQuote:
        updated = _number(row.get("_updated"))
        as_of = (
            datetime.fromtimestamp(updated / 1000, tz=timezone.utc)
            if updated else datetime.now(timezone.utc)
        )
        availability = _availability(row.get("6509"))
        warnings = []
        if availability == MarketDataAvailability.NOT_SUBSCRIBED:
            warnings.append("IBKR reports no market-data subscription")
        elif availability in {
            MarketDataAvailability.DELAYED,
            MarketDataAvailability.FROZEN,
            MarketDataAvailability.FROZEN_DELAYED,
        }:
            warnings.append(f"market data is {availability.value}")
        elif availability in {
            MarketDataAvailability.INCOMPLETE,
            MarketDataAvailability.UNKNOWN,
        }:
            warnings.append("market-data availability is incomplete or unknown")
        return MarketQuote(
            provider=PROVIDER,
            conid=int(row["conid"]),
            symbol=symbol or row.get("55"),
            as_of=as_of,
            availability=availability,
            bid=_number(row.get("84")),
            ask=_number(row.get("86")),
            last=_number(row.get("31")),
            mark=_number(row.get("7635")),
            bid_size=_number(row.get("88")),
            ask_size=_number(row.get("85")),
            last_size=_number(row.get("7059")),
            volume=_number(row.get("87")),
            warnings=warnings,
        )

    def quote_underlying(self, symbol: str) -> MarketQuote:
        underlying = self.resolve_underlying(symbol)
        row = self._snapshot([underlying.conid]).get(underlying.conid)
        if not row:
            raise IBKRMarketDataError(f"IBKR returned no quote for {symbol.upper()}")
        return self._quote(row, symbol=underlying.symbol)

    def option_chain(
        self, symbol: str, month: str, strikes: Sequence[float]
    ) -> OptionChainSnapshot:
        month = _month(month)
        selected = sorted({float(strike) for strike in strikes})
        if not selected or len(selected) > MAX_CHAIN_STRIKES:
            raise ValueError(
                f"select 1-{MAX_CHAIN_STRIKES} explicit strikes per chain request"
            )
        strike_set = self.available_strikes(symbol, month)
        contracts: list[OptionContract] = []
        for strike in selected:
            if strike in strike_set.call_strikes:
                contracts.append(
                    self._option_contract(
                        strike_set.underlying, month, strike, "C"
                    )
                )
            if strike in strike_set.put_strikes:
                contracts.append(
                    self._option_contract(
                        strike_set.underlying, month, strike, "P"
                    )
                )
        if not contracts:
            raise IBKRMarketDataError("none of the requested strikes are valid")

        underlying_quote = self.quote_underlying(strike_set.underlying.symbol)
        rows = self._snapshot([contract.conid for contract in contracts])
        options = []
        for contract in contracts:
            row = rows.get(contract.conid)
            if not row:
                continue
            iv = _number(row.get("7633"))
            options.append(
                OptionQuote(
                    contract=contract,
                    quote=self._quote(row, symbol=contract.symbol),
                    implied_volatility=(iv / 100.0 if iv is not None else None),
                    delta=_number(row.get("7308")),
                    gamma=_number(row.get("7309")),
                    theta=_number(row.get("7310")),
                    vega=_number(row.get("7311")),
                    open_interest=_number(row.get("7638")),
                )
            )
        warnings = []
        if len(options) != len(contracts):
            warnings.append(
                f"quotes returned for {len(options)} of {len(contracts)} contracts"
            )
        return OptionChainSnapshot(
            provider=PROVIDER,
            underlying=strike_set.underlying,
            underlying_quote=underlying_quote,
            month=month,
            options=options,
            warnings=warnings,
        )
