"""Daily price + corporate-action ingestion from Yahoo Finance's public chart
endpoint (keyless). Provides split-adjusted closes and total-return-adjusted closes plus explicit
split/dividend events, which the point-in-time design requires.

Phase 2 should still move to a licensed vendor for survivorship-free history
and delisted names."""
from __future__ import annotations

from datetime import datetime, timezone
import time
from zoneinfo import ZoneInfo

import httpx

from ..provenance import save_raw
from ..market_calendar import latest_completed_session

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


class YahooPriceError(RuntimeError):
    """A provider-safe failure suitable for workflow diagnostics."""


# Yahoo intermittently refuses requests from cloud runners (HTTP 429 or 5xx,
# sometimes a stale cached series); the same stocks succeed minutes later.
ATTEMPTS_PER_HOST = 3
BACKOFF_SECONDS = (2.0, 4.0)
MAX_RETRY_AFTER_SECONDS = 20.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _retry_after(resp) -> float | None:
    try:
        return min(MAX_RETRY_AFTER_SECONDS, max(0.0, float(resp.headers.get("Retry-After", ""))))
    except (TypeError, ValueError):
        return None


def _payload(symbol: str) -> tuple[dict, str]:
    errors = []
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        url = (f"https://{host}/v8/finance/chart/{symbol}"
               f"?range=15y&interval=1d&events=div%2Csplits")
        for attempt in range(ATTEMPTS_PER_HOST):
            wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
            try:
                resp = httpx.get(url, timeout=60, headers=_UA, follow_redirects=True)
                if resp.status_code != 200:
                    if resp.status_code in RETRYABLE_STATUS:
                        wait = _retry_after(resp) or wait
                    raise YahooPriceError(f"YAHOO_HTTP_{resp.status_code}")
                payload = resp.json()
                chart = payload.get("chart") if isinstance(payload, dict) else None
                result = (chart or {}).get("result") or []
                if not result:
                    code = ((chart or {}).get("error") or {}).get("code") or "EMPTY_RESULT"
                    raise YahooPriceError(f"YAHOO_{str(code).upper()}")
                # A successful HTTP response can still be a cached, stale
                # series. Retry both hosts before handing off to FMP.
                required = latest_completed_session()
                bars = result[0]
                closes = ((bars.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
                days = [datetime.fromtimestamp(ts, timezone.utc).astimezone(
                    ZoneInfo("America/New_York")).date().isoformat()
                    for ts, close in zip(bars.get("timestamp") or [], closes)
                    if close is not None and close > 0]
                if required not in days:
                    raise YahooPriceError("YAHOO_LATEST_COMPLETED_SESSION_MISSING")
                return payload, url
            except (httpx.HTTPError, ValueError, YahooPriceError) as exc:
                detail = str(exc) if isinstance(exc, YahooPriceError) else type(exc).__name__.upper()
                errors.append(f"{host.split('.')[0]}:{detail}")
                if detail == "YAHOO_HTTP_404":
                    break
                if attempt < ATTEMPTS_PER_HOST - 1:
                    time.sleep(wait)
    raise YahooPriceError("YAHOO_ALL_ENDPOINTS_FAILED[" + ",".join(errors) + "]")


def fetch_daily(ticker: str) -> tuple[list[dict], list[dict]]:
    """Returns (price_rows ascending, corporate_actions).

    price_rows: {date, open, high, low, close, adj_close, volume} (close is
    split-adjusted, without dividend adjustments). corporate_actions: {date, action_type, value} where value is
    the dividend amount or the split ratio (e.g. 4.0 for 4:1)."""
    symbol = ticker.upper()
    payload, url = _payload(symbol)
    save_raw("prices", [symbol, "yahoo_chart_daily"], payload, url)

    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    adj = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose", [])

    def _day(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(ZoneInfo("America/New_York")).date().isoformat()

    rows = []
    for i, ts in enumerate(timestamps):
        close = quote["close"][i]
        if close is None:
            continue
        rows.append({
            "date": _day(ts),
            "open": quote["open"][i], "high": quote["high"][i],
            "low": quote["low"][i], "close": close,
            "adj_close": adj[i] if i < len(adj) else close,
            "volume": quote["volume"][i] or 0,
        })
    rows.sort(key=lambda r: r["date"])
    # Yahoo occasionally repeats the live session's timestamp; keep last
    dedup: dict[str, dict] = {r["date"]: r for r in rows}
    rows = [dedup[d] for d in sorted(dedup)]

    actions = []
    events = result.get("events", {})
    for div in (events.get("dividends") or {}).values():
        actions.append({"date": _day(div["date"]), "action_type": "dividend",
                        "value": div["amount"]})
    for sp in (events.get("splits") or {}).values():
        ratio = sp["numerator"] / sp["denominator"]
        actions.append({"date": _day(sp["date"]), "action_type": "split",
                        "value": ratio})
    actions.sort(key=lambda a: a["date"])
    return rows, actions
