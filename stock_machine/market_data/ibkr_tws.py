"""Read-only market data via the TWS/IB Gateway socket API (ibapi).

Third IBKR path in this codebase, complementing:
  - ingestion/ibkr.py          -> Flex statements (positions/trades, no live data)
  - market_data/ibkr_client_portal.py -> Client Portal REST gateway

Why this one: the TWS socket API is the standard desktop path — it needs no
web gateway, and it exposes delayed data (market-data type 3) without a paid
subscription, which the Client Portal route does not reliably do.

READ-ONLY BY CONSTRUCTION: this module never imports or calls placeOrder;
the wrapper handles only contract, tick, and error callbacks. Trading is out
of scope for this system.

Connection: TWS/IB Gateway must be running with "Enable ActiveX and Socket
Clients" checked. Default ports: 7497 TWS paper, 7496 TWS live,
4002 Gateway paper, 4001 Gateway live.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from collections.abc import Sequence
from datetime import date, datetime, timezone

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper

from ..config import PROJECT_ROOT  # noqa: F401  (import loads .env)
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

PROVIDER = "ibkr_tws"

# ibapi tick type ids we consume (delayed variants included)
_LIVE_TICKS = {
    1: "bid", 2: "ask", 4: "last",
    0: "bid_size", 3: "ask_size", 5: "last_size",
    8: "volume", 6: "high", 7: "low", 9: "close", 14: "open",
    22: "open_interest", 27: "open_interest", 28: "open_interest",
}
_DELAYED_TICKS = {
    66: "bid", 67: "ask", 68: "last",
    69: "bid_size", 70: "ask_size", 71: "last_size",
    74: "volume", 72: "high", 73: "low", 75: "close", 76: "open",
    86: "open_interest",
}

# No US equity trades anywhere near 10bn shares in a session. IBKR's delayed
# volume tick (74) has been observed returning values ~1e13 for liquid names;
# a wrong volume is worse than none, so implausible values are dropped and
# reported rather than published.
MAX_PLAUSIBLE_VOLUME = 1e10

# market data type: 1 live, 2 frozen, 3 delayed, 4 delayed-frozen
def _market_data_type() -> int:
    """Read at call time so .env is always in effect."""
    return int(os.environ.get("IBKR_TWS_MARKET_DATA_TYPE", "3"))


class IBKRTWSError(RuntimeError):
    """Raised for connection or request failures."""


@dataclass
class TWSSettings:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 17
    timeout_s: float = 12.0

    @classmethod
    def from_env(cls) -> "TWSSettings":
        return cls(
            host=os.environ.get("IBKR_TWS_HOST", "127.0.0.1"),
            port=int(os.environ.get("IBKR_TWS_PORT", "7497")),
            client_id=int(os.environ.get("IBKR_TWS_CLIENT_ID", "17")),
            timeout_s=float(os.environ.get("IBKR_TWS_TIMEOUT_S", "12")),
        )


class _Wrapper(EWrapper):
    """Collects callbacks; no order methods are implemented, by design."""

    def __init__(self) -> None:
        EWrapper.__init__(self)
        self.next_id: int | None = None
        self.contracts: dict[int, list] = {}
        self.ticks: dict[int, dict] = {}
        self.delayed: dict[int, bool] = {}
        self.rejected: dict[int, list[str]] = {}
        self.greeks: dict[int, dict] = {}
        self.sec_def: dict[int, list[dict]] = {}
        self.errors: list[tuple[int, int, str]] = []
        self.done: dict[int, threading.Event] = {}
        self.connected_evt = threading.Event()

    # --- lifecycle ---
    def nextValidId(self, orderId: int) -> None:
        self.next_id = orderId
        self.connected_evt.set()

    def error(self, reqId, errorTime, errorCode=None, errorString="",
              advancedOrderRejectJson="") -> None:
        # ibapi 10.x passes errorTime; older builds do not. Normalize.
        if errorCode is None or isinstance(errorCode, str):
            errorCode, errorString = errorTime, errorCode or ""
        self.errors.append((reqId, errorCode, str(errorString)))
        # 2104/2106/2158 are benign "market data farm OK" notices
        if errorCode not in (2104, 2106, 2107, 2158, 2119) and reqId in self.done:
            self.done[reqId].set()

    # --- contract resolution ---
    def contractDetails(self, reqId: int, contractDetails) -> None:
        self.contracts.setdefault(reqId, []).append(contractDetails)

    def contractDetailsEnd(self, reqId: int) -> None:
        if reqId in self.done:
            self.done[reqId].set()

    # --- option definitions ---
    def securityDefinitionOptionParameter(
        self, reqId, exchange, underlyingConId, tradingClass, multiplier,
        expirations, strikes,
    ) -> None:
        self.sec_def.setdefault(reqId, []).append({
            "exchange": exchange,
            "expirations": sorted(str(e) for e in expirations),
            "strikes": sorted(float(s) for s in strikes),
        })

    def securityDefinitionOptionParameterEnd(self, reqId: int) -> None:
        if reqId in self.done:
            self.done[reqId].set()

    def tickOptionComputation(
        self, reqId, tickType, tickAttrib, impliedVol, delta, optPrice,
        pvDividend, gamma, vega, theta, undPrice,
    ) -> None:
        """Model computation (13 live / 83 delayed) is the usable greek set;
        bid/ask/last computations are noisier and only fill gaps."""
        row = self.greeks.setdefault(reqId, {})
        preferred = tickType in (13, 83)
        for key, value in (("iv", impliedVol), ("delta", delta),
                           ("gamma", gamma), ("vega", vega),
                           ("theta", theta), ("opt_price", optPrice)):
            if value is None:
                continue
            # ibapi uses sentinels for "not computed"
            if value == -1 or value != value or abs(value) > 1e100:
                continue
            if preferred or key not in row:
                row[key] = float(value)
        if tickType in (80, 81, 82, 83):
            self.delayed[reqId] = True

    # --- ticks ---
    def tickPrice(self, reqId, tickType, price, attrib) -> None:
        self._store(reqId, tickType, price)

    def tickSize(self, reqId, tickType, size) -> None:
        self._store(reqId, tickType, float(size))

    def _store(self, reqId: int, tickType: int, value: float) -> None:
        if value is None or value < 0:
            return  # -1 means "no data" in ibapi
        field = _LIVE_TICKS.get(tickType)
        delayed = False
        if field is None:
            field = _DELAYED_TICKS.get(tickType)
            delayed = field is not None
        if field is None:
            return
        if field == "volume" and value > MAX_PLAUSIBLE_VOLUME:
            # provider-side anomaly: record it, never publish it
            self.rejected.setdefault(reqId, []).append(
                f"volume {value:.0f} exceeds plausible maximum "
                f"{MAX_PLAUSIBLE_VOLUME:.0f}; dropped as a provider anomaly"
            )
            return
        self.ticks.setdefault(reqId, {})[field] = value
        if delayed:
            self.delayed[reqId] = True


class IBKRTWSMarketData:
    """Read-only TWS/IB Gateway client implementing MarketDataProvider."""

    def __init__(self, settings: TWSSettings | None = None) -> None:
        self.settings = settings or TWSSettings.from_env()
        self._wrapper = _Wrapper()
        self._client = EClient(self._wrapper)
        self._thread: threading.Thread | None = None
        self._req_seq = 1000

    # --- connection ---
    def connect(self) -> None:
        s = self.settings
        self._client.connect(s.host, s.port, s.client_id)
        self._thread = threading.Thread(target=self._client.run, daemon=True)
        self._thread.start()
        if not self._wrapper.connected_evt.wait(s.timeout_s):
            self.close()
            raise IBKRTWSError(
                f"no handshake from TWS/Gateway at {s.host}:{s.port} within "
                f"{s.timeout_s}s. Is TWS running with 'Enable ActiveX and "
                "Socket Clients' enabled, and is the port correct "
                "(7497 TWS paper / 7496 live / 4002 Gateway paper / 4001 live)?"
            )
        self._client.reqMarketDataType(_market_data_type())

    def close(self) -> None:
        try:
            self._client.disconnect()
        except Exception:
            pass

    def __enter__(self) -> "IBKRTWSMarketData":
        self.connect()
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _next_req(self) -> int:
        self._req_seq += 1
        return self._req_seq

    # --- provider interface ---
    def session_status(self) -> SessionStatus:
        connected = bool(self._client.isConnected())
        return SessionStatus(
            provider=PROVIDER,
            connected=connected,
            authenticated=connected and self._wrapper.next_id is not None,
            message=(f"TWS socket {self.settings.host}:{self.settings.port}, "
                     f"market data type {_market_data_type()} "
                     f"({'delayed' if _market_data_type() in (3, 4) else 'live'})"),
        )

    @staticmethod
    def _stock(symbol: str) -> Contract:
        c = Contract()
        c.symbol = symbol.upper()
        c.secType = "STK"
        c.currency = "USD"
        c.exchange = "SMART"
        return c

    def resolve_underlying(self, symbol: str) -> UnderlyingContract:
        req = self._next_req()
        evt = threading.Event()
        self._wrapper.done[req] = evt
        self._client.reqContractDetails(req, self._stock(symbol))
        if not evt.wait(self.settings.timeout_s):
            raise IBKRTWSError(f"contract lookup for {symbol} timed out")
        details = self._wrapper.contracts.get(req) or []
        if not details:
            errs = "; ".join(m for r, _, m in self._wrapper.errors if r == req)
            raise IBKRTWSError(f"no contract found for {symbol}. {errs}")
        d = details[0]
        con = d.contract
        return UnderlyingContract(
            provider=PROVIDER,
            symbol=con.symbol,
            conid=int(con.conId),
            name=(getattr(d, "longName", None) or None),
            currency=con.currency or "USD",
            exchange=con.primaryExchange or con.exchange,
            has_options=bool(getattr(d, "orderTypes", "")),
        )

    def quote_underlying(self, symbol: str) -> MarketQuote:
        contract = self.resolve_underlying(symbol)
        req = self._next_req()
        evt = threading.Event()
        self._wrapper.done[req] = evt
        c = self._stock(symbol)
        c.conId = contract.conid
        c.exchange = "SMART"
        self._client.reqMktData(req, c, "", True, False, [])
        # snapshot=True ends on its own; poll until price fields land
        deadline = time.monotonic() + self.settings.timeout_s
        while time.monotonic() < deadline:
            row = self._wrapper.ticks.get(req, {})
            if any(k in row for k in ("bid", "ask", "last")):
                break
            time.sleep(0.1)
        self._client.cancelMktData(req)
        row = self._wrapper.ticks.get(req, {})
        bid, ask = row.get("bid"), row.get("ask")
        mark = (bid + ask) / 2 if bid is not None and ask is not None else row.get("last")
        availability = (MarketDataAvailability.DELAYED
                        if self._wrapper.delayed.get(req)
                        else MarketDataAvailability.REALTIME)
        warnings: list[str] = []
        if not row:
            availability = MarketDataAvailability.UNKNOWN
            errs = [m for r, code, m in self._wrapper.errors
                    if r == req and code not in (2104, 2106, 2107, 2158, 2119)]
            warnings.append("no ticks returned" + (f": {errs[0]}" if errs else ""))
        warnings.extend(self._wrapper.rejected.get(req, []))
        return MarketQuote(
            provider=PROVIDER,
            conid=contract.conid,
            symbol=contract.symbol,
            as_of=datetime.now(timezone.utc),
            availability=availability,
            bid=bid, ask=ask, last=row.get("last"), mark=mark,
            bid_size=row.get("bid_size"), ask_size=row.get("ask_size"),
            last_size=row.get("last_size"),
            volume=row.get("volume"),
            warnings=warnings,
        )

    # --- option chain support ---
    def _sec_def_params(self, symbol: str, conid: int) -> tuple[list[str], list[float]]:
        req = self._next_req()
        evt = threading.Event()
        self._wrapper.done[req] = evt
        self._client.reqSecDefOptParams(req, symbol.upper(), "", "STK", conid)
        if not evt.wait(self.settings.timeout_s):
            raise IBKRTWSError(f"option parameters for {symbol} timed out")
        rows = self._wrapper.sec_def.get(req) or []
        if not rows:
            raise IBKRTWSError(f"no option parameters returned for {symbol}")
        # prefer SMART routing when present; it carries the full strike ladder
        row = next((r for r in rows if r["exchange"] == "SMART"), rows[0])
        return sorted(row["expirations"]), sorted(row["strikes"])

    @staticmethod
    def _month_key(yyyymmdd: str) -> str:
        months = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                  "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
        return f"{months[int(yyyymmdd[4:6]) - 1]}{yyyymmdd[2:4]}"

    def _expiration_for_month(self, expirations: list[str], month: str) -> str:
        matches = [e for e in expirations if self._month_key(e) == month.upper()]
        if not matches:
            raise IBKRTWSError(
                f"no expiration in {month}; available: "
                f"{sorted({self._month_key(e) for e in expirations})}"
            )
        # standard monthly expiry is the third Friday — the latest in the month
        return max(matches)

    def available_strikes(self, symbol: str, month: str) -> StrikeSet:
        underlying = self.resolve_underlying(symbol)
        expirations, strikes = self._sec_def_params(symbol, underlying.conid)
        self._expiration_for_month(expirations, month)  # validates the month
        return StrikeSet(
            provider=PROVIDER,
            underlying=underlying,
            month=month.upper(),
            call_strikes=strikes,
            put_strikes=strikes,
        )

    def _option_contract(
        self, symbol: str, expiry: str, strike: float, right: str,
        underlying_conid: int,
    ) -> OptionContract:
        c = Contract()
        c.symbol = symbol.upper()
        c.secType = "OPT"
        c.currency = "USD"
        c.exchange = "SMART"
        c.lastTradeDateOrContractMonth = expiry
        c.strike = float(strike)
        c.right = right
        c.multiplier = "100"
        req = self._next_req()
        evt = threading.Event()
        self._wrapper.done[req] = evt
        self._client.reqContractDetails(req, c)
        if not evt.wait(self.settings.timeout_s):
            raise IBKRTWSError(f"contract lookup timed out: {symbol} {expiry} "
                               f"{strike}{right}")
        details = self._wrapper.contracts.get(req) or []
        if not details:
            raise IBKRTWSError(
                f"no listed contract: {symbol} {expiry} {strike}{right}")
        con = details[0].contract
        return OptionContract(
            provider=PROVIDER,
            conid=int(con.conId),
            symbol=con.symbol,
            underlying_conid=underlying_conid,
            expiration=date(int(expiry[0:4]), int(expiry[4:6]), int(expiry[6:8])),
            strike=float(con.strike),
            right=con.right,
            multiplier=int(con.multiplier or 100),
            currency=con.currency or "USD",
            exchange=con.exchange or "SMART",
            description=getattr(details[0], "longName", None) or None,
        )

    def _option_snapshot(
        self, contract: OptionContract
    ) -> tuple[dict, dict, int]:
        """Returns (tick row, greeks row, req id) for one option contract.

        Uses STREAMING market data: IBKR rejects snapshot requests that carry
        a generic tick list with error 321, and open interest / implied vol
        are only available as generic ticks 101 / 106.
        """
        c = Contract()
        c.conId = contract.conid
        c.symbol = contract.symbol
        c.secType = "OPT"
        c.currency = contract.currency
        c.exchange = contract.exchange
        c.lastTradeDateOrContractMonth = contract.expiration.strftime("%Y%m%d")
        c.strike = contract.strike
        c.right = contract.right
        c.multiplier = str(contract.multiplier)
        req = self._next_req()
        # 101 = option open interest; 106 = option implied volatility.
        # snapshot MUST be False here (see docstring).
        self._client.reqMktData(req, c, "101,106", False, False, [])
        deadline = time.monotonic() + self.settings.timeout_s
        while time.monotonic() < deadline:
            row = self._wrapper.ticks.get(req, {})
            has_price = any(k in row for k in ("bid", "ask", "last"))
            if has_price and self._wrapper.greeks.get(req):
                break
            time.sleep(0.1)
        self._client.cancelMktData(req)
        time.sleep(0.05)  # gentle pacing; IBKR throttles bursts
        return (self._wrapper.ticks.get(req, {}),
                self._wrapper.greeks.get(req, {}), req)

    def option_chain(
        self, symbol: str, month: str, strikes: Sequence[float]
    ) -> OptionChainSnapshot:
        if not strikes:
            raise IBKRTWSError("at least one strike is required")
        underlying = self.resolve_underlying(symbol)
        underlying_quote = self.quote_underlying(symbol)
        expirations, ladder = self._sec_def_params(symbol, underlying.conid)
        expiry = self._expiration_for_month(expirations, month)

        warnings: list[str] = []
        unlisted = [s for s in strikes if not any(abs(s - k) < 1e-6 for k in ladder)]
        if unlisted:
            warnings.append(
                f"strikes not listed for {symbol} {month}: "
                f"{', '.join(str(s) for s in unlisted)}")

        options: list[OptionQuote] = []
        for strike in strikes:
            if strike in unlisted:
                continue
            for right in ("C", "P"):
                try:
                    contract = self._option_contract(
                        symbol, expiry, strike, right, underlying.conid)
                    row, greeks, tick_req = self._option_snapshot(
                        contract)
                except IBKRTWSError as exc:
                    warnings.append(str(exc))
                    continue
                bid, ask = row.get("bid"), row.get("ask")
                mark = ((bid + ask) / 2 if bid is not None and ask is not None
                        else row.get("last") or greeks.get("opt_price"))
                quote = MarketQuote(
                    provider=PROVIDER,
                    conid=contract.conid,
                    symbol=contract.symbol,
                    as_of=datetime.now(timezone.utc),
                    availability=(MarketDataAvailability.DELAYED
                                  if self._wrapper.delayed.get(
                                      tick_req, True)
                                  else MarketDataAvailability.REALTIME),
                    bid=bid, ask=ask, last=row.get("last"), mark=mark,
                    bid_size=row.get("bid_size"), ask_size=row.get("ask_size"),
                    last_size=row.get("last_size"), volume=row.get("volume"),
                )
                options.append(OptionQuote(
                    contract=contract,
                    quote=quote,
                    implied_volatility=greeks.get("iv"),
                    delta=greeks.get("delta"),
                    gamma=greeks.get("gamma"),
                    theta=greeks.get("theta"),
                    vega=greeks.get("vega"),
                    open_interest=row.get("open_interest"),
                ))
        if options and not any(o.implied_volatility is not None
                               or o.delta is not None for o in options):
            warnings.append(
                "greeks unavailable: IBKR does not compute model greeks on "
                "delayed data (market data type 3). Position greeks will be "
                "incomplete; a real-time options subscription is required."
            )
        if not options:
            raise IBKRTWSError(
                f"no option quotes returned for {symbol} {month}. "
                + ("; ".join(warnings) if warnings else ""))
        return OptionChainSnapshot(
            provider=PROVIDER,
            underlying=underlying,
            underlying_quote=underlying_quote,
            month=month.upper(),
            options=options,
            warnings=warnings,
        )
