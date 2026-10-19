"""Point-in-time earnings announcements from SEC 8-K item 2.02 filings.

An issuer files an 8-K with item 2.02 (Results of Operations and Financial
Condition) when it releases results. The SEC records the filing's acceptance
time in UTC (acceptanceDateTime; verified against releases before the open and
after the close, which shift with daylight saving as true UTC does). The
reaction session is the first exchange close after acceptance, so an event can
never be used before it was public.

Every item 2.02 filing is an event. A filing is labelled PRIMARY unless another
item 2.02 filing by the same company was accepted in the previous CLUSTER_DAYS
days (CLUSTERED). The label looks only backwards in time and is descriptive:
it is not used to select events, because a same-day officer or restructuring
item can precede the full results release by a few days (e.g. NKE, June 2026).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..market_calendar import next_close_after

VERSION = "sec-8k-202-announcements.v1"
CLUSTER_DAYS = 30
START = "2015-06-01"
# Companies whose current SEC registrant replaced an earlier one inside the window:
# (predecessor CIK, the predecessor's SEC name must contain, last date used).
PREDECESSORS = {
    "DIS": [("0001001039", "TWDC", "2019-03-19")],    # The Walt Disney Company before its 2019 holding-company reorganization
    "AVGO": [("0001649338", "BROADCOM", "2018-04-04")],
}


def filings(payloads: list[dict]) -> list[dict]:
    """Merge the columnar 'recent' arrays and every paginated page, deduplicated."""
    seen, out = set(), []
    for payload in payloads:
        block = payload.get("filings", {}).get("recent", payload)
        keys = ("accessionNumber", "form", "filingDate", "acceptanceDateTime", "items")
        if not all(k in block for k in keys):
            continue
        for row in zip(*(block[k] for k in keys)):
            record = dict(zip(keys, row))
            if record["accessionNumber"] not in seen:
                seen.add(record["accessionNumber"])
                out.append(record)
    return out


def _acceptance(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("SEC_ACCEPTANCE_WITHOUT_ZONE")
    return value


def announcements(ticker: str, cik: str, records: list[dict], *, until: str | None = None,
                  since: str = START) -> list[dict]:
    """Item 2.02 8-K events for one registrant, oldest first, with roles."""
    picked = []
    for r in records:
        items = [i.strip() for i in (r.get("items") or "").split(",")]
        if r.get("form") != "8-K" or "2.02" not in items or not r.get("acceptanceDateTime"):
            continue
        accepted = _acceptance(r["acceptanceDateTime"])
        day = accepted.date().isoformat()
        if day < since or (until and day > until):
            continue
        picked.append({"ticker": ticker, "cik": cik, "accession": r["accessionNumber"], "filing_date": r["filingDate"],
                       "acceptance_utc": accepted.isoformat(), "items": ",".join(items),
                       "reaction_session": next_close_after(accepted.isoformat())})
    return label_roles(picked)


def label_roles(events: list[dict]) -> list[dict]:
    """Sort by acceptance and label each event against the one before it."""
    events.sort(key=lambda e: e["acceptance_utc"])
    previous = None
    for e in events:
        accepted = datetime.fromisoformat(e["acceptance_utc"])
        e["role"] = "CLUSTERED" if previous is not None and accepted - previous < timedelta(days=CLUSTER_DAYS) \
            else "PRIMARY"
        previous = accepted
    return events


def validate(events: list[dict], *, reviewed_accessions: dict, first_year: dict, last_year: int) -> dict:
    """Coverage and cross-check gates (descriptive; never used to select events)."""
    by_ticker = {}
    for e in events:
        if e["role"] == "PRIMARY":
            by_ticker.setdefault(e["ticker"], {}).setdefault(e["reaction_session"][:4], 0)
            by_ticker[e["ticker"]][e["reaction_session"][:4]] += 1
    gaps = {}
    for ticker, start in first_year.items():
        years = by_ticker.get(ticker, {})
        thin = {str(y): years.get(str(y), 0) for y in range(start, last_year) if years.get(str(y), 0) < 3}
        if thin:
            gaps[ticker] = thin
    primary = {e["accession"] for e in events if e["role"] == "PRIMARY"}
    every = {e["accession"] for e in events}
    reviewed = {t: [a for a in accs if a not in every] for t, accs in reviewed_accessions.items()}
    return {"tickers": len(by_ticker), "primary_events": len(primary),
            "clustered_events": sum(e["role"] == "CLUSTERED" for e in events),
            "years_with_fewer_than_3_primary": gaps,
            "reviewed_release_accessions_missing": {t: v for t, v in reviewed.items() if v}}
