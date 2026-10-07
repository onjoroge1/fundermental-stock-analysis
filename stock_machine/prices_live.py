"""On-demand coherent completed-session price refresh.

Full history and its manifest share one adjustment vintage. Fundamentals,
forecasts and analyst narratives still require their separate refresh paths.
"""
from __future__ import annotations

import concurrent.futures
from datetime import date, datetime, timezone

from . import db

TAIL_DAYS_DEFAULT = 10
MAX_WORKERS = 8


def refresh_ticker(ticker: str, days: int = TAIL_DAYS_DEFAULT,
                   prefer: str = "auto") -> dict:
    """Refresh one coherent adjustment vintage and its dated manifest.

    `days` remains API-compatible; a partial adjusted-price tail cannot be
    safely spliced into an older corporate-action vintage.
    """
    from .market_health import refresh_prices
    ticker = ticker.upper()
    if prefer != "auto":
        return {"ticker": ticker, "status": "error", "source": None,
                "error": "Use the configured PRICE_SOURCE for a coherent full-series refresh"}
    try:
        with db.connect() as conn:
            result = refresh_prices(conn, [ticker], only_if_stale=False, limit=1)
        row = next((r for r in result.get("results", []) if r.get("ticker") == ticker), {})
        if result["status"] not in {"OK", "PARTIAL_RECOVERED"}:
            return {"ticker": ticker, "status": "error", "source": row.get("source"),
                    "error": "Completed-session price refresh failed", "refresh": result}
        return {"ticker": ticker, "status": "ok", "source": row.get("source"),
                "latest_date": result["health"]["latest_market_date"],
                "refresh": result}
    except Exception as exc:
        return {"ticker": ticker, "status": "error", "source": None,
                "error": type(exc).__name__}


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


def benchmark_tickers() -> list[str]:
    """SPY/QQQ plus every mapped sector ETF — the same set
    scripts/refresh_prices.py --benchmarks uses."""
    from .regime import SECTOR_ETF

    return sorted({"SPY", "QQQ", *SECTOR_ETF.values()})


def refresh_universe(days: int = TAIL_DAYS_DEFAULT, prefer: str = "auto") -> dict:
    """Every series the store tracks: covered companies AND the benchmark
    ETFs. Regime and benchmark-relative evaluation read the ETF series, so
    leaving them behind would make 'prices current' silently untrue for the
    comparisons that depend on them (price_status counts every series)."""
    with db.connect() as conn:
        tickers = [c["ticker"] for c in db.list_companies(conn)]
    return refresh_many(sorted(set(tickers) | set(benchmark_tickers())),
                        days, prefer)


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
