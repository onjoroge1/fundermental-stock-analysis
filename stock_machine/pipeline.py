"""End-to-end pipeline for one ticker: ingest raw → normalize → load Postgres."""
from __future__ import annotations

from . import db
from .config import ensure_dirs
from .ingestion import estimates as est_ing
from .ingestion import prices as price_ing
from .ingestion import sec as sec_ing
from .normalization import financial_periods as fp


def run(ticker: str) -> dict:
    ticker = ticker.upper()
    ensure_dirs()

    sec_data = sec_ing.ingest(ticker)
    price_rows, corporate_actions = price_ing.fetch_daily(ticker)
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
        db.replace_actions(conn, ticker, corporate_actions)
        db.replace_shares(conn, ticker, shares)
        from datetime import date
        snap_count = db.insert_consensus_snapshots(
            conn, ticker, date.today().isoformat(), est["snapshots"])
        db.upsert_surprises(conn, ticker, est["surprises"])
        db.record_events(conn, ticker, restatement_events + est["events"])
        from .ingestion import form4
        form4_stats = form4.ingest_from_submissions(
            conn, ticker, sec_data["cik"], sub)
    finally:
        conn.close()

    return {
        "ticker": ticker,
        "cik": sec_data["cik"],
        "quarters": len(quarterly),
        "annual_periods": len(annual),
        "price_rows": len(price_rows),
        "corporate_actions": len(corporate_actions),
        "filings": len(filings),
        "restatement_events": len(restatement_events),
        "consensus_snapshots": snap_count,
        "earnings_surprises": len(est["surprises"]),
        **form4_stats,
    }
