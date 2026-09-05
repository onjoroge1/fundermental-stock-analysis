"""End-to-end pipeline for one ticker: ingest raw → normalize → load Postgres."""
from __future__ import annotations

import os

from . import db
from .config import ensure_dirs
from .ingestion import estimates as est_ing
from .ingestion import prices as price_ing
from .ingestion import sec as sec_ing
from .normalization import financial_periods as fp
from .data_quality import assess_dataset
from .market_calendar import latest_completed_session


PRICE_SOURCE = os.environ.get("PRICE_SOURCE", "auto").lower()


def _completed_prices(rows):
    cutoff = latest_completed_session()
    return [r for r in rows if r["date"] <= cutoff]


def _fetch_prices(ticker: str) -> tuple[list[dict], list[dict], str, list[dict]]:
    """Daily prices, preferring the broker and falling back to Yahoo.

    IB Gateway is a desktop application that must be running and logged in,
    and IBKR forces a daily logout — so a hard dependency on it would break
    the scheduled refresh on any morning it happens to be closed. The source
    actually used is returned and recorded, so a series is never silently
    mixed or silently downgraded.

    PRICE_SOURCE=tws forces the broker (raising if unavailable), =yahoo forces
    Yahoo, =auto (default) tries the broker then falls back.
    """
    events: list[dict] = []
    if PRICE_SOURCE in ("tws", "auto"):
        try:
            from .ingestion import prices_tws

            rows, actions = prices_tws.fetch_daily(ticker)
            return _completed_prices(rows), actions, "ibkr_tws", events
        except Exception as exc:
            if PRICE_SOURCE == "tws":
                raise
            events.append({
                "event": "PRICE_SOURCE_FALLBACK",
                "dataset": "prices",
                "detail": (f"IBKR TWS unavailable ({type(exc).__name__}: "
                           f"{exc}); fell back to Yahoo for the full series"),
            })
    rows, actions = price_ing.fetch_daily(ticker)
    return _completed_prices(rows), actions, "yahoo", events


def run(ticker: str) -> dict:
    ticker = ticker.upper()
    ensure_dirs()

    sec_data = sec_ing.ingest(ticker)
    price_rows, corporate_actions, price_source, price_events = _fetch_prices(
        ticker)
    est = est_ing.fetch_estimates(ticker)

    quarterly, annual, restatement_events = fp.build_periods(
        sec_data["companyfacts"])
    shares = fp.extract_shares_outstanding(sec_data["companyfacts"])

    sub = sec_data["submissions"]
    recent = sub.get("filings", {}).get("recent", {})
    filings = []
    for i, accn in enumerate(recent.get("accessionNumber", [])):
        form = recent["form"][i]
        if form not in ("10-K", "10-Q", "10-K/A", "10-Q/A", "8-K"):
            continue
        filings.append({
            "accession_number": accn,
            "form": form,
            "filed_at": recent["filingDate"][i] or None,
            "report_date": recent["reportDate"][i] or None,
            "primary_document": recent["primaryDocument"][i] or None,
        })

    conn = db.connect()
    try:
        db.init_schema(conn)
        from .sectors import classify
        db.upsert_company(conn, {
            "ticker": ticker,
            "cik": sec_data["cik"],
            "legal_name": sub.get("name") or sec_data["title"],
            "exchange": (sub.get("exchanges") or [None])[0],
            "sic_description": sub.get("sicDescription"),
            "fiscal_year_end": sub.get("fiscalYearEnd"),
            "sic": sub.get("sic"),
            "sector": classify(sub.get("sic")),
        })
        db.replace_filings(conn, ticker, filings)
        db.replace_periods(conn, ticker, quarterly, annual)
        db.replace_prices(conn, ticker, price_rows)
        # Only replace corporate actions when the source actually supplied
        # them. IBKR historical bars carry no split/dividend events, so an
        # unconditional replace would DELETE the action history that share-
        # count split adjustment depends on.
        if corporate_actions:
            db.replace_actions(conn, ticker, corporate_actions)
        elif price_source != "yahoo":
            price_events.append({
                "event": "CORPORATE_ACTIONS_RETAINED",
                "dataset": "corporate_actions",
                "detail": (f"{price_source} supplies no corporate actions; "
                           "existing split/dividend history retained"),
            })
        db.replace_shares(conn, ticker, shares)
        from datetime import date
        snap_count = db.insert_consensus_snapshots(
            conn, ticker, date.today().isoformat(), est["snapshots"])
        db.upsert_surprises(conn, ticker, est["surprises"])
        db.record_events(conn, ticker,
                         restatement_events + est["events"] + price_events)
        from .ingestion import form4
        form4_stats = form4.ingest_from_submissions(
            conn, ticker, sec_data["cik"], sub)
        dataset_rows = {
            "fundamentals": quarterly + annual,
            "prices": price_rows,
            "filings": filings,
            "shares": shares,
            "consensus": est["snapshots"],
            "earnings_surprises": est["surprises"],
            "corporate_actions": corporate_actions,
        }
        snapshots = []
        for name, rows in dataset_rows.items():
            snapshot = assess_dataset(name, rows)
            # Preserve replayable normalized vintages where revisions matter.
            # Price history is already row-addressable by date and would make
            # full-history JSON copies grow quadratically.
            if name != "prices":
                snapshot["payload"] = rows
            snapshots.append(snapshot)
        db.record_dataset_snapshots(conn, ticker, snapshots)
    finally:
        conn.close()

    return {
        "ticker": ticker,
        "cik": sec_data["cik"],
        "quarters": len(quarterly),
        "annual_periods": len(annual),
        "price_rows": len(price_rows),
        "price_source": price_source,
        "corporate_actions": len(corporate_actions),
        "filings": len(filings),
        "restatement_events": len(restatement_events),
        "consensus_snapshots": snap_count,
        "earnings_surprises": len(est["surprises"]),
        "dataset_quality": {s["dataset"]: s["status"] for s in snapshots},
        **form4_stats,
    }
