"""Market-data freshness health and lightweight price refresh.

This module intentionally operates only on daily prices. It does not run SEC,
fundamentals, estimates, forecasts, or narrative analysis. The public health
surface is read-only; write callers must authenticate at the API layer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from . import db
from .data_quality import assess_dataset
from .pipeline import _fetch_prices

DEFAULT_MAX_AGE_HOURS = 18.0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hours_since(ts: datetime | None, now: datetime) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts.astimezone(timezone.utc)).total_seconds() / 3600.0)


def health(conn, *, max_age_hours: float = DEFAULT_MAX_AGE_HOURS) -> dict:
    """Return one auditable freshness view over every covered ticker."""
    now = _now_utc()
    companies = db.list_companies(conn)
    tickers = [c["ticker"] for c in companies]

    with conn.cursor() as cur:
        cur.execute(
            """SELECT ticker, max(date)::text AS latest_market_date
               FROM prices_daily GROUP BY ticker"""
        )
        latest_dates = {ticker: market_date for ticker, market_date in cur.fetchall()}
        cur.execute(
            """SELECT DISTINCT ON (ticker)
                      ticker, observed_at, max_record_date::text, status,
                      metrics, reasons
               FROM dataset_snapshots
               WHERE dataset='prices'
               ORDER BY ticker, observed_at DESC"""
        )
        snapshots = {
            row[0]: {
                "observed_at": row[1],
                "snapshot_market_date": row[2],
                "snapshot_status": row[3],
                "metrics": row[4] or {},
                "reasons": row[5] or [],
            }
            for row in cur.fetchall()
        }

    rows = []
    for ticker in tickers:
        snap = snapshots.get(ticker) or {}
        observed_at = snap.get("observed_at")
        age_hours = _hours_since(observed_at, now)
        latest_market_date = latest_dates.get(ticker)
        stale = age_hours is None or age_hours > max_age_hours
        missing = latest_market_date is None
        state = "MISSING" if missing else ("STALE" if stale else "CURRENT")
        rows.append({
            "ticker": ticker,
            "state": state,
            "latest_market_date": latest_market_date,
            "last_fetched_at": observed_at.isoformat() if observed_at else None,
            "age_hours": round(age_hours, 2) if age_hours is not None else None,
            "snapshot_market_date": snap.get("snapshot_market_date"),
            "snapshot_status": snap.get("snapshot_status"),
        })

    stale_rows = [r for r in rows if r["state"] != "CURRENT"]
    market_dates = [r["latest_market_date"] for r in rows if r["latest_market_date"]]
    observed = [r["last_fetched_at"] for r in rows if r["last_fetched_at"]]
    overall = "HEALTHY" if not stale_rows else ("ERROR" if not market_dates else "STALE")
    return {
        "status": overall,
        "checked_at": now.isoformat(),
        "max_age_hours": float(max_age_hours),
        "ticker_count": len(rows),
        "current_count": sum(r["state"] == "CURRENT" for r in rows),
        "stale_count": sum(r["state"] == "STALE" for r in rows),
        "missing_count": sum(r["state"] == "MISSING" for r in rows),
        "latest_market_date": max(market_dates) if market_dates else None,
        "oldest_market_date": min(market_dates) if market_dates else None,
        "last_successful_price_fetch_at": max(observed) if observed else None,
        "stale_tickers": [r["ticker"] for r in stale_rows],
        "tickers": rows,
    }


def refresh_prices(
    conn,
    tickers: Iterable[str],
    *,
    only_if_stale: bool = True,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    limit: int | None = None,
) -> dict:
    """Refresh daily price history for selected tickers and persist provenance."""
    wanted = []
    seen = set()
    for raw in tickers:
        ticker = raw.upper().strip()
        if ticker and ticker not in seen:
            wanted.append(ticker)
            seen.add(ticker)
    if limit is not None:
        wanted = wanted[: max(0, int(limit))]

    before = health(conn, max_age_hours=max_age_hours)
    stale = set(before["stale_tickers"])
    if only_if_stale:
        wanted = [t for t in wanted if t in stale]

    results = []
    failures = []
    for ticker in wanted:
        try:
            price_rows, corporate_actions, price_source, price_events = _fetch_prices(ticker)
            db.replace_prices(conn, ticker, price_rows)
            if corporate_actions:
                db.replace_actions(conn, ticker, corporate_actions)
            if price_events:
                db.record_events(conn, ticker, price_events)
            snapshot = assess_dataset("prices", price_rows)
            db.record_dataset_snapshots(conn, ticker, [snapshot])
            results.append({
                "ticker": ticker,
                "status": "OK",
                "source": price_source,
                "rows": len(price_rows),
                "latest_market_date": price_rows[-1]["date"] if price_rows else None,
            })
        except Exception as exc:
            failures.append({
                "ticker": ticker,
                "error": f"{type(exc).__name__}: {exc}",
            })

    after = health(conn, max_age_hours=max_age_hours)
    return {
        "status": "OK" if not failures else "PARTIAL",
        "requested": len(wanted),
        "refreshed": len(results),
        "failures": failures,
        "results": results,
        "health": after,
    }
