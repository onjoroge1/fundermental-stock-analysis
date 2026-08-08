"""SEC EDGAR ingestion: ticker→CIK mapping, submissions, and XBRL companyfacts.
All responses are stored verbatim in immutable raw storage before any use."""
from __future__ import annotations

import time

import httpx

from ..config import SEC_MIN_REQUEST_INTERVAL_S, SEC_USER_AGENT
from ..provenance import save_raw

_last_request_at = 0.0


def _get(url: str) -> httpx.Response:
    global _last_request_at
    wait = SEC_MIN_REQUEST_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    resp = httpx.get(
        url,
        headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        timeout=60,
        follow_redirects=True,
    )
    _last_request_at = time.monotonic()
    resp.raise_for_status()
    return resp


def resolve_cik(ticker: str) -> tuple[str, str]:
    """Return (zero-padded 10-digit CIK, SEC-registered title) for a ticker.
    CIK is the primary identity; the ticker is a display identifier."""
    url = "https://www.sec.gov/files/company_tickers.json"
    payload = _get(url).json()
    save_raw("sec", ["company_tickers"], payload, url)
    ticker = ticker.upper()
    for row in payload.values():
        if row["ticker"].upper() == ticker:
            return f"{row['cik_str']:010d}", row["title"]
    raise ValueError(f"Ticker {ticker!r} not found in SEC company_tickers.json")


def fetch_submissions(ticker: str, cik: str) -> dict:
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    payload = _get(url).json()
    save_raw("sec", [ticker.upper(), "submissions"], payload, url)
    return payload


def fetch_companyfacts(ticker: str, cik: str) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    payload = _get(url).json()
    save_raw("sec", [ticker.upper(), "companyfacts"], payload, url)
    return payload


def ingest(ticker: str) -> dict:
    """Full SEC pull for one ticker. Returns dict with cik/title/submissions/facts."""
    cik, title = resolve_cik(ticker)
    submissions = fetch_submissions(ticker, cik)
    facts = fetch_companyfacts(ticker, cik)
    return {"ticker": ticker.upper(), "cik": cik, "title": title,
            "submissions": submissions, "companyfacts": facts}
