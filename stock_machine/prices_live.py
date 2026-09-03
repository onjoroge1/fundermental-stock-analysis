"""On-demand price refresh.

The daily pipeline is the system of record for full history, but it is slow
(SEC + consensus + insiders per ticker) and only runs on a schedule. This
module updates ONLY the recent price tail, so prices can be brought current
in seconds at any moment — before marking the paper book, before a scan, or
whenever the UI shows a stale figure.

Source order matches the pipeline: IBKR when Gateway is reachable, Yahoo
otherwise, with the source recorded per run. Rows are upserted by date, so a
refresh mid-session updates today's row rather than appending a duplicate.

What this does NOT do: rebuild fundamentals, consensus, insiders or bundles.
It touches prices only, so a refreshed price never implies refreshed
analysis.
"""
from __future__ import annotations

import concurrent.futures
from datetime import date, datetime, timezone

from . import db

TAIL_DAYS_DEFAULT = 10
MAX_WORKERS = 8


def _yahoo_tail(ticker: str, days: int) -> list[dict]:
    """Recent daily bars from Yahoo, including today's in-progress bar."""
    import httpx

    span = "5d" if days <= 5 else ("1mo" if days <= 30 else "3mo")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}"
           f"?range={span}&interval=1d")
    resp = httpx.get(url, timeout=30,
                     headers={"User-Agent": "Mozilla/5.0 (Macintosh)"})
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    adj = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose", [])
    rows = []
    for i, ts in enumerate(stamps):
        close = quote["close"][i]
        if close is None:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().date()
        rows.append({
            "date": day.isoformat(),
            "open": quote["open"][i], "high": quote["high"][i],
            "low": quote["low"][i], "close": close,
            "adj_close": adj[i] if i < len(adj) and adj[i] is not None else close,
            "volume": quote["volume"][i] or 0,
        })
    return rows


def _tws_tail(ticker: str, days: int) -> list[dict]:
    """Recent bars from IB Gateway. Raises if the broker is unreachable."""
    from .ingestion import prices_tws

    duration = f"{max(days, 5)} D"
    rows, _ = prices_tws.fetch_daily(ticker, duration=duration)
    return rows


def refresh_ticker(ticker: str, days: int = TAIL_DAYS_DEFAULT,
                   prefer: str = "auto") -> dict:
    """Refresh one ticker's recent price tail. Never raises: a failure is
    reported so a partial refresh is visible rather than silent."""
    ticker = ticker.upper()
    source, rows, error = None, [], None
    if prefer in ("tws", "auto"):
        try:
            rows, source = _tws_tail(ticker, days), "ibkr_tws"
        except Exception as exc:
            if prefer == "tws":
                return {"ticker": ticker, "status": "error", "source": "ibkr_tws",
                        "error": f"{type(exc).__name__}: {exc}"}
            error = f"{type(exc).__name__}"
    if not rows:
        try:
            rows, source = _yahoo_tail(ticker, days), "yahoo"
        except Exception as exc:
            return {"ticker": ticker, "status": "error", "source": "yahoo",
                    "error": f"{type(exc).__name__}: {exc}"}
    if not rows:
        return {"ticker": ticker, "status": "no_data", "source": source}

    with db.connect() as conn:
        written = db.upsert_prices(conn, ticker, rows)
    latest = rows[-1]
    return {
        "ticker": ticker, "status": "ok", "source": source,
        "rows_written": written, "latest_date": latest["date"],
        "latest_close": round(latest["close"], 4),
        "broker_fallback": error,
    }


def refresh_many(tickers: list[str], days: int = TAIL_DAYS_DEFAULT,
                 prefer: str = "auto") -> dict:
    """Refresh many tickers concurrently.

    Yahoo tolerates parallel requests; the broker path does not (one socket,
    paced requests), so a broker-preferred refresh runs serially.
    """
    started = datetime.now(timezone.utc)
    if prefer in ("tws",):
        results = [refresh_ticker(t, days, prefer) for t in tickers]
    else:
        with concurrent.futures.ThreadPoolExecutor(MAX_WORKERS) as pool:
            results = list(pool.map(
                lambda t: refresh_ticker(t, days, prefer), tickers))
    ok = [r for r in results if r["status"] == "ok"]
    latest = max((r["latest_date"] for r in ok), default=None)
    return {
        "refreshed_at": started.isoformat(timespec="seconds"),
        "seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        "requested": len(tickers), "ok": len(ok),
        "failed": len(results) - len(ok),
        "latest_price_date": latest,
        "sources": sorted({r["source"] for r in ok if r.get("source")}),
        "results": results,
    }


def refresh_universe(days: int = TAIL_DAYS_DEFAULT, prefer: str = "auto") -> dict:
    with db.connect() as conn:
        tickers = [c["ticker"] for c in db.list_companies(conn)]
    return refresh_many(tickers, days, prefer)


def price_status() -> dict:
    """How current are stored prices? Drives the UI's staleness banner."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT max(date), count(DISTINCT ticker) FROM prices_daily")
            latest, tickers = cur.fetchone()
            cur.execute(
                """SELECT count(*) FROM (
                     SELECT ticker, max(date) AS d FROM prices_daily
                     GROUP BY ticker) t WHERE t.d < %s""", (latest,))
            behind = cur.fetchone()[0]
    age = (date.today() - latest).days if latest else None
    return {
        "latest_price_date": latest.isoformat() if latest else None,
        "tickers": tickers,
        "tickers_behind_latest": behind,
        "age_days": age,
        # markets close on weekends/holidays, so 1-3 days is normal
        "stale": age is not None and age > 4,
    }
