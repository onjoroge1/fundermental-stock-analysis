"""Daily price + corporate-action ingestion from Yahoo Finance's public chart
endpoint (keyless). Provides BOTH unadjusted and adjusted closes plus explicit
split/dividend events, which the point-in-time design requires.

Phase 2 should still move to a licensed vendor for survivorship-free history
and delisted names."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ..provenance import save_raw

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def fetch_daily(ticker: str) -> tuple[list[dict], list[dict]]:
    """Returns (price_rows ascending, corporate_actions).

    price_rows: {date, open, high, low, close, adj_close, volume} (close is
    unadjusted). corporate_actions: {date, action_type, value} where value is
    the dividend amount or the split ratio (e.g. 4.0 for 4:1)."""
    symbol = ticker.upper()
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range=15y&interval=1d&events=div%2Csplits")
    resp = httpx.get(url, timeout=60, headers=_UA, follow_redirects=True)
    resp.raise_for_status()
    payload = resp.json()
    save_raw("prices", [symbol, "yahoo_chart_daily"], payload, url)

    result = payload["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    adj = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose", [])

    def _day(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().date().isoformat()

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
