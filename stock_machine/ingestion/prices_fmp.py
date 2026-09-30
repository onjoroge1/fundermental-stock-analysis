"""Credential-safe FMP fallback for completed-session OHLCV prices."""
from __future__ import annotations

from math import isfinite

import httpx

from ..config import FMP_API_KEY
from ..provenance import save_raw

BASE = "https://financialmodelingprep.com"


class FmpPriceError(RuntimeError):
    """An FMP failure safe to expose in operational diagnostics."""


def _request(path: str, params: dict) -> list[dict]:
    if not FMP_API_KEY:
        raise FmpPriceError("FMP_PRICE_FALLBACK_NOT_CONFIGURED")
    try:
        response = httpx.get(
            f"{BASE}{path}",
            params={**params, "apikey": FMP_API_KEY},
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise FmpPriceError(f"FMP_PRICE_HTTP_{type(exc).__name__.upper()}") from exc
    if response.status_code != 200:
        raise FmpPriceError(f"FMP_PRICE_HTTP_{response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise FmpPriceError("FMP_PRICE_NON_JSON") from exc
    if not isinstance(payload, list):
        raise FmpPriceError("FMP_PRICE_UNEXPECTED_PAYLOAD")
    return payload


def _number(row: dict, *names: str, required: bool = False) -> float | None:
    value = next((row.get(name) for name in names if row.get(name) is not None), None)
    if value is None:
        if required:
            raise FmpPriceError("FMP_PRICE_REQUIRED_VALUE_MISSING")
        return None
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise FmpPriceError("FMP_PRICE_VALUE_INVALID") from exc
    if not isfinite(value) or (required and value <= 0):
        raise FmpPriceError("FMP_PRICE_VALUE_INVALID")
    return value


def _row(value: dict, *, expected_date: str | None = None) -> dict:
    day = str(value.get("date") or "")[:10]
    if not day or (expected_date and day != expected_date):
        raise FmpPriceError("FMP_PRICE_DATE_INVALID")
    close = _number(value, "close", "price", required=True)
    adjusted = _number(value, "adjClose", "adjustedClose")
    volume = _number(value, "volume")
    return {
        "date": day,
        "open": _number(value, "open"),
        "high": _number(value, "high"),
        "low": _number(value, "low"),
        "close": close,
        "adj_close": adjusted if adjusted and adjusted > 0 else close,
        "volume": volume if volume is not None and volume >= 0 else 0.0,
    }


def fetch_eod_bulk(market_date: str, tickers: list[str]) -> dict[str, dict]:
    """Return one exact completed-session row per requested symbol."""
    wanted = {ticker.upper() for ticker in tickers}
    payload = _request("/stable/eod-bulk", {"date": market_date})
    save_raw("prices", [market_date, "fmp_eod_bulk"], payload,
             f"{BASE}/stable/eod-bulk?date={market_date}")
    result: dict[str, dict] = {}
    for value in payload:
        symbol = str(value.get("symbol") or "").upper()
        if symbol in wanted:
            result[symbol] = _row(value, expected_date=market_date)
    return result


def fetch_daily(ticker: str) -> tuple[list[dict], list[dict]]:
    """Full-history fallback for the normal per-ticker ingestion pipeline."""
    symbol = ticker.upper()
    payload = _request("/stable/historical-price-eod/full", {"symbol": symbol})
    save_raw("prices", [symbol, "fmp_historical_price_eod_full"], payload,
             f"{BASE}/stable/historical-price-eod/full?symbol={symbol}")
    rows = [_row(value) for value in payload]
    dedup = {row["date"]: row for row in rows}
    return [dedup[day] for day in sorted(dedup)], []
