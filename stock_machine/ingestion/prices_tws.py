"""Daily price history from IB Gateway / TWS.

Two requests per symbol, because one series cannot serve both jobs:
  TRADES         split-adjusted close  -> `close`, used for market cap
  ADJUSTED_LAST  split+dividend-adj    -> `adj_close`, used for returns

NEVER splice sources inside one symbol's series. Yahoo's adjustment
convention and IBKR's differ, so a mid-series join manufactures a fake
return at the seam. A run takes a whole series from one provider or falls
back entirely.

IBKR paces historical requests (roughly 60 per 10 minutes), so requests are
spaced; a 53-name universe takes ~15-20 minutes and is meant for the daily
job, not a web request.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from ..provenance import save_raw

PROVIDER = "ibkr_tws"
# IBKR allows ~60 historical requests per 10 minutes; 10s spacing stays under.
REQUEST_SPACING_S = float(os.environ.get("IBKR_TWS_HIST_SPACING_S", "10"))
DEFAULT_DURATION = os.environ.get("IBKR_TWS_HIST_DURATION", "15 Y")


class TWSHistoryError(RuntimeError):
    """Raised when the broker path cannot supply a usable series."""


def _parse_bar_date(raw: str) -> str:
    """IBKR formatDate=1 yields 'YYYYMMDD' for daily bars."""
    text = str(raw).split()[0]
    return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"


def merge_series(trades: list[dict], adjusted: list[dict]) -> list[dict]:
    """Join the two IBKR series on date.

    A date present in TRADES but missing from ADJUSTED_LAST keeps a null
    adj_close rather than borrowing the unadjusted close: silently
    substituting one for the other would understate historical returns
    across every dividend.
    """
    adj_by_date = {row["date"]: row["close"] for row in adjusted}
    out = []
    for row in trades:
        out.append({
            "date": row["date"],
            "open": row["open"], "high": row["high"], "low": row["low"],
            "close": row["close"],
            "adj_close": adj_by_date.get(row["date"]),
            "volume": row["volume"],
        })
    return out


def fetch_daily(ticker: str, duration: str | None = None) -> tuple[list[dict], list[dict]]:
    """Return (price_rows, corporate_actions) for one symbol.

    Corporate actions come back empty: IBKR historical bars do not carry
    split/dividend events, so the caller must keep whatever action history
    it already holds rather than assume none exist.
    """
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.wrapper import EWrapper

    duration = duration or DEFAULT_DURATION
    symbol = ticker.upper()

    class _Wrapper(EWrapper):
        def __init__(self) -> None:
            EWrapper.__init__(self)
            self.ready = threading.Event()
            self.bars: dict[int, list] = {}
            self.done: dict[int, threading.Event] = {}
            self.errors: list[tuple] = []

        def nextValidId(self, orderId: int) -> None:
            self.ready.set()

        def error(self, reqId, errorTime, errorCode=None, errorString="",
                  advancedOrderRejectJson="") -> None:
            code, text = errorCode, errorString
            if code is None or isinstance(code, str):
                code, text = errorTime, (errorCode or "")
            if code in (2104, 2106, 2107, 2158, 2119, 2100):
                return          # benign connection-status notices
            self.errors.append((reqId, code, str(text)[:160]))
            evt = self.done.get(reqId)
            if evt:
                evt.set()       # unblock the waiter; caller inspects errors

        def historicalData(self, reqId, bar) -> None:
            self.bars.setdefault(reqId, []).append(bar)

        def historicalDataEnd(self, reqId, start, end) -> None:
            evt = self.done.get(reqId)
            if evt:
                evt.set()

    wrapper = _Wrapper()
    client = EClient(wrapper)
    host = os.environ.get("IBKR_TWS_HOST", "127.0.0.1")
    port = int(os.environ.get("IBKR_TWS_PORT", "4001"))
    client_id = int(os.environ.get("IBKR_TWS_HIST_CLIENT_ID", "23"))
    timeout = float(os.environ.get("IBKR_TWS_HIST_TIMEOUT_S", "120"))

    client.connect(host, port, client_id)
    threading.Thread(target=client.run, daemon=True).start()
    if not wrapper.ready.wait(30) or client.serverVersion() is None:
        client.disconnect()
        raise TWSHistoryError(
            f"no TWS/Gateway handshake at {host}:{port}. Is IB Gateway "
            "running and logged in?"
        )

    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.currency = "USD"
    contract.exchange = "SMART"

    series: dict[str, list[dict]] = {}
    try:
        for index, what in enumerate(("TRADES", "ADJUSTED_LAST")):
            if index:
                time.sleep(REQUEST_SPACING_S)
            req_id = 900 + index
            evt = threading.Event()
            wrapper.done[req_id] = evt
            client.reqHistoricalData(
                req_id, contract, "", duration, "1 day", what, 1, 1, False, [])
            if not evt.wait(timeout):
                raise TWSHistoryError(f"{symbol} {what} request timed out")
            bars = wrapper.bars.get(req_id, [])
            if not bars:
                errs = "; ".join(f"{c}: {m}" for _, c, m in wrapper.errors)
                raise TWSHistoryError(
                    f"{symbol} {what} returned no bars. {errs}")
            series[what] = [
                {"date": _parse_bar_date(b.date), "open": float(b.open),
                 "high": float(b.high), "low": float(b.low),
                 "close": float(b.close), "volume": float(b.volume or 0)}
                for b in bars
            ]
    finally:
        client.disconnect()

    rows = merge_series(series["TRADES"], series["ADJUSTED_LAST"])
    rows.sort(key=lambda r: r["date"])
    save_raw("prices", [symbol, "ibkr_tws_daily"],
             {"duration": duration, "rows": rows,
              "retrieved_at": datetime.utcnow().isoformat()},
             f"tws://{host}:{port}/historical/{symbol}")
    return rows, []
