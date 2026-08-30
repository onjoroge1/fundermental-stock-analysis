"""Point-in-time company-event ingestion for P2-E.

The automation gate needs to distinguish "no event scheduled" from "we do not
know whether an event is scheduled".  This module therefore returns both event
rows and per-event-type coverage state.

FMP stable calendar endpoints are preferred because a bounded from/to query can
establish negative coverage for the whole window.  Symbol endpoints are used
only as a degraded fallback and are labelled PARTIAL: seeing no row in a
history-oriented endpoint is not sufficient evidence that no future event
exists.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import httpx

from ..config import FMP_API_KEY
from ..provenance import save_raw

BASE = "https://financialmodelingprep.com"
SOURCE = "fmp"
DEFAULT_HORIZON_DAYS = 370
_CACHE: dict[tuple[str, str, str], tuple[list[dict] | None, dict | None]] = {}


def _request(path: str, params: dict[str, Any]) -> tuple[list[dict] | None, dict | None]:
    if not FMP_API_KEY:
        return None, {
            "coverage_status": "UNAVAILABLE",
            "reason": "FMP_API_KEY is not configured",
        }
    time.sleep(0.25)
    try:
        response = httpx.get(
            f"{BASE}{path}", params={**params, "apikey": FMP_API_KEY}, timeout=45
        )
    except httpx.HTTPError as exc:
        return None, {
            "coverage_status": "ERROR",
            "reason": f"{type(exc).__name__}: {exc}",
        }
    body = response.text
    if response.status_code in {401, 402, 403} or body.startswith("Premium"):
        return None, {
            "coverage_status": "PLAN_LIMIT",
            "reason": body[:240] or f"HTTP {response.status_code}",
        }
    if response.status_code != 200:
        return None, {
            "coverage_status": "ERROR",
            "reason": f"HTTP {response.status_code}: {body[:240]}",
        }
    try:
        payload = response.json()
    except ValueError:
        return None, {
            "coverage_status": "ERROR",
            "reason": f"non-JSON response: {body[:240]}",
        }
    if not isinstance(payload, list):
        return None, {
            "coverage_status": "ERROR",
            "reason": f"unexpected payload type {type(payload).__name__}",
        }
    return payload, None


def _calendar(path: str, start: date, end: date) -> tuple[list[dict] | None, dict | None]:
    key = (path, start.isoformat(), end.isoformat())
    if key not in _CACHE:
        _CACHE[key] = _request(
            path, {"from": start.isoformat(), "to": end.isoformat()}
        )
        payload, error = _CACHE[key]
        if payload is not None:
            save_raw(
                "company_events",
                [path.rsplit("/", 1)[-1], start.isoformat(), end.isoformat()],
                payload,
                f"{BASE}{path}?from={start.isoformat()}&to={end.isoformat()}",
            )
    return _CACHE[key]


def _symbol_fallback(path: str, ticker: str) -> tuple[list[dict] | None, dict | None]:
    payload, error = _request(path, {"symbol": ticker, "limit": 40})
    if payload is not None:
        save_raw(
            "company_events", [ticker, path.rsplit("/", 1)[-1]], payload,
            f"{BASE}{path}?symbol={ticker}",
        )
    return payload, error


def _symbol(row: dict) -> str:
    return str(row.get("symbol") or row.get("ticker") or "").upper()


def _coverage(event_type: str, status: str, start: date, end: date,
              *, method: str, detail: dict | None = None) -> dict:
    return {
        "event_type": event_type,
        "coverage_status": status,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "detail": {"method": method, **(detail or {})},
    }


def _normalize_earnings(rows: list[dict], ticker: str,
                        start: date, end: date) -> list[dict]:
    out = []
    for row in rows:
        if _symbol(row) and _symbol(row) != ticker:
            continue
        raw_date = row.get("date")
        if not raw_date:
            continue
        event_date = str(raw_date)[:10]
        if not (start.isoformat() <= event_date <= end.isoformat()):
            continue
        actual = row.get("epsActual")
        out.append({
            "event_type": "EARNINGS",
            "event_date": event_date,
            "status": "REPORTED" if actual is not None else "SCHEDULED",
            "metadata": {
                "time": row.get("time"),
                "fiscal_date_ending": row.get("fiscalDateEnding"),
                "eps_estimated": row.get("epsEstimated"),
                "revenue_estimated": row.get("revenueEstimated"),
                "updated_from_date": row.get("updatedFromDate"),
            },
        })
    return out


def _normalize_dividends(rows: list[dict], ticker: str,
                         start: date, end: date) -> list[dict]:
    out = []
    for row in rows:
        if _symbol(row) and _symbol(row) != ticker:
            continue
        # FMP's stable dividend contract uses `date` as the ex-dividend date.
        raw_date = row.get("date") or row.get("exDividendDate")
        if not raw_date:
            continue
        event_date = str(raw_date)[:10]
        if not (start.isoformat() <= event_date <= end.isoformat()):
            continue
        out.append({
            "event_type": "EX_DIVIDEND",
            "event_date": event_date,
            "status": "SCHEDULED" if event_date >= date.today().isoformat() else "HISTORICAL",
            "metadata": {
                "dividend": row.get("dividend"),
                "adjusted_dividend": row.get("adjDividend"),
                "declaration_date": row.get("declarationDate"),
                "record_date": row.get("recordDate"),
                "payment_date": row.get("paymentDate"),
                "frequency": row.get("frequency"),
            },
        })
    return out


def _normalize_splits(rows: list[dict], ticker: str,
                      start: date, end: date) -> list[dict]:
    out = []
    for row in rows:
        if _symbol(row) and _symbol(row) != ticker:
            continue
        raw_date = row.get("date") or row.get("splitDate")
        if not raw_date:
            continue
        event_date = str(raw_date)[:10]
        if not (start.isoformat() <= event_date <= end.isoformat()):
            continue
        out.append({
            "event_type": "SPLIT",
            "event_date": event_date,
            "status": "SCHEDULED" if event_date >= date.today().isoformat() else "HISTORICAL",
            "metadata": {
                "numerator": row.get("numerator"),
                "denominator": row.get("denominator"),
                "split_ratio": row.get("splitRatio"),
            },
        })
    return out


def _load_type(ticker: str, event_type: str, calendar_path: str,
               fallback_path: str, normalizer, start: date, end: date
               ) -> tuple[list[dict], dict]:
    payload, error = _calendar(calendar_path, start, end)
    if payload is not None:
        events = normalizer(payload, ticker, start, end)
        return events, _coverage(
            event_type, "AVAILABLE", start, end, method="bounded_calendar",
            detail={"provider_rows": len(payload)},
        )

    fallback, fallback_error = _symbol_fallback(fallback_path, ticker)
    if fallback is not None:
        events = normalizer(fallback, ticker, start, end)
        return events, _coverage(
            event_type, "PARTIAL", start, end, method="symbol_fallback",
            detail={
                "calendar_error": error,
                "provider_rows": len(fallback),
                "warning": "symbol fallback cannot prove absence of a future event",
            },
        )

    status = (error or fallback_error or {}).get("coverage_status", "ERROR")
    return [], _coverage(
        event_type, status, start, end, method="unavailable",
        detail={"calendar_error": error, "fallback_error": fallback_error},
    )


def fetch_company_events(ticker: str, *, as_of: date | None = None,
                         horizon_days: int = DEFAULT_HORIZON_DAYS) -> dict:
    symbol = ticker.upper()
    observed = as_of or date.today()
    start = observed - timedelta(days=7)
    end = observed + timedelta(days=horizon_days)

    earnings, earnings_coverage = _load_type(
        symbol, "EARNINGS", "/stable/earnings-calendar", "/stable/earnings",
        _normalize_earnings, start, end,
    )
    dividends, dividend_coverage = _load_type(
        symbol, "EX_DIVIDEND", "/stable/dividends-calendar", "/stable/dividends",
        _normalize_dividends, start, end,
    )
    splits, split_coverage = _load_type(
        symbol, "SPLIT", "/stable/splits-calendar", "/stable/splits",
        _normalize_splits, start, end,
    )

    return {
        "ticker": symbol,
        "source": SOURCE,
        "observed_on": observed.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "events": earnings + dividends + splits,
        "coverage": [earnings_coverage, dividend_coverage, split_coverage],
    }
