"""Normalize periodic filings that SEC companyfacts has not picked up yet.

companyfacts is built from each filing's XBRL, but it can lag, or miss, a
filing that the submissions index already lists (Ford's Q2 2026 10-Q, filed
2026-07-29, was absent in October). For the newest 10-Q/10-K filings with no
facts in companyfacts, read the filing's own inline XBRL and add its
consolidated (non-dimensional) us-gaap facts in companyfacts form. Existing
facts are never replaced; each supplement records the filing URL and content
hash so provenance points at the filing, not at companyfacts.
"""
from __future__ import annotations

import hashlib
from typing import Callable

from .inline_xbrl import InlineXbrl, fact_value, unit_name

PERIODIC_FORMS = ("10-Q", "10-K")
MAX_LAGGING_FILINGS = 4


def filing_facts(html: str, *, cik: str, accession: str, filed: str, form: str) -> dict:
    """{tag: {"units": {unit: [entry]}}} for this entity's consolidated facts."""
    parser = InlineXbrl()
    parser.feed(html)
    fy = parser.dei.get("dei:DocumentFiscalYearFocus") or ""
    fp = parser.dei.get("dei:DocumentFiscalPeriodFocus")
    out: dict = {}
    seen: set = set()
    for f in parser.facts:
        prefix, _, name = f["tag"].partition(":")
        context = parser.contexts.get(f["context_id"]) or {}
        entity = context.get("entity") or ""
        unit = unit_name(parser.units.get(f["unit_id"]) or {})
        if (prefix != "us-gaap" or context.get("dimensional") or not unit
                or not entity.isdigit() or int(entity) != int(cik)):
            continue
        end = context.get("instant") or context.get("end")
        start = None if context.get("instant") else context.get("start")
        value = fact_value(f)
        if not end or value is None or (not context.get("instant") and not start):
            continue
        key = (name, unit, start, end)
        if key in seen:  # inline XBRL repeats a fact wherever it is displayed
            continue
        seen.add(key)
        entry = {"end": end, "val": int(value) if value == value.to_integral_value() else float(value),
                 "accn": accession, "fy": int(fy) if fy.isdigit() else None, "fp": fp,
                 "form": form, "filed": filed}
        if start:
            entry["start"] = start
        out.setdefault(name, {"units": {}})["units"].setdefault(unit, []).append(entry)
    return out


def lagging_filings(submissions: dict, companyfacts: dict, *, limit: int = MAX_LAGGING_FILINGS) -> list[dict]:
    """Newest periodic filings with no facts in companyfacts, newest first.

    Stops at the first filing companyfacts already covers: older gaps are not a
    lag (they predate the dataset or were never XBRL)."""
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    covered = {e.get("accn") for tag in gaap.values() for rows in tag.get("units", {}).values() for e in rows}
    recent = submissions.get("filings", {}).get("recent", {})
    inline = recent.get("isInlineXBRL") or [1] * len(recent.get("form", []))
    rows = sorted(
        (i for i, form in enumerate(recent.get("form", [])) if form in PERIODIC_FORMS),
        key=lambda i: recent["filingDate"][i], reverse=True)
    out = []
    for i in rows:
        accession = recent["accessionNumber"][i]
        if accession in covered:
            break
        if inline[i]:
            out.append({"accession": accession, "form": recent["form"][i], "filed": recent["filingDate"][i],
                        "report_date": recent["reportDate"][i], "primary_document": recent["primaryDocument"][i]})
        if len(out) >= limit:
            break
    return out


def merge(companyfacts: dict, supplement: dict) -> int:
    """Add entries absent from companyfacts; never replace an existing one."""
    gaap = companyfacts.setdefault("facts", {}).setdefault("us-gaap", {})
    added = 0
    for name, spec in supplement.items():
        units = gaap.setdefault(name, {"units": {}}).setdefault("units", {})
        for unit, entries in spec["units"].items():
            rows = units.setdefault(unit, [])
            present = {(e.get("start"), e.get("end"), e.get("accn")) for e in rows}
            for e in entries:
                if (e.get("start"), e["end"], e["accn"]) not in present:
                    rows.append(e)
                    added += 1
    return added


def supplement_lagging_filings(ticker: str, cik: str, submissions: dict, companyfacts: dict, *,
                               fetch: Callable[[str], bytes] | None = None,
                               save: Callable[[str, str, bytes, str], None] | None = None) -> list[dict]:
    """Merge lagging filings into companyfacts in place; returns events."""
    if fetch is None:
        from .sec import _get

        def fetch(url):
            return _get(url).content
    events = []
    for filing in lagging_filings(submissions, companyfacts):
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
               f"{filing['accession'].replace('-', '')}/{filing['primary_document']}")
        try:
            raw = fetch(url)
            facts = filing_facts(raw.decode("utf-8", "replace"), cik=cik, accession=filing["accession"],
                                 filed=filing["filed"], form=filing["form"])
            added = merge(companyfacts, facts)
            if save:
                save(ticker, filing["accession"], raw, url)
            record = {**filing, "source_url": url, "source_content_sha256": hashlib.sha256(raw).hexdigest(),
                      "facts_added": added}
            companyfacts.setdefault("_filing_supplements", []).append(record)
            events.append({"event": "FILING_XBRL_SUPPLEMENTED", "dataset": "fundamentals", **record})
        except Exception as exc:
            events.append({"event": "FILING_XBRL_UNAVAILABLE", "dataset": "fundamentals",
                           "accession": filing["accession"], "source_url": url, "detail": type(exc).__name__})
    return events
