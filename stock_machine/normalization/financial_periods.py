"""Assemble point-in-time quarterly and annual financial periods from SEC
companyfacts.

Policies (each one maps to a stress-test row in the spec):
- First-reported wins: for a (field, period) the value from the EARLIEST filing
  is canonical and `available_at` is the day after its latest dependency filing. Later filings that
  disagree are logged as restatement events, never silently substituted —
  point-in-time reconstruction must reflect what was known on the analysis date.
- Duration classification is explicit: a "quarter" flow fact spans 60–115 days,
  an "annual" one 330–390 (covers 53-week fiscal years). Anything else is
  ignored (cumulative 6/9-month contexts are the main duplicate-fact trap).
- Q4 is derived as FY minus Q1..Q3 for additive fields only, and is stamped
  `available_at` = the latest dependency availability, including the 10-K.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .xbrl_mapping import (FIELD_MAP, FLOW_FIELDS, NON_ADDITIVE_FIELDS,
                           units_for)

QUARTER_DAYS = (60, 115)
SEMI_DAYS = (150, 210)
THREE_QUARTER_DAYS = (240, 300)
ANNUAL_DAYS = (330, 390)

# cumulative year-to-date contexts (cash-flow statements in 10-Qs are YTD,
# never discrete quarters after Q1) — used for quarterly differencing
CUMULATIVE_TYPES = ("quarter", "semi", "three_quarters", "annual")


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _duration_type(start: str | None, end: str) -> str | None:
    if start is None:
        return "instant"
    days = (_parse_date(end) - _parse_date(start)).days
    if QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]:
        return "quarter"
    if SEMI_DAYS[0] <= days <= SEMI_DAYS[1]:
        return "semi"
    if THREE_QUARTER_DAYS[0] <= days <= THREE_QUARTER_DAYS[1]:
        return "three_quarters"
    if ANNUAL_DAYS[0] <= days <= ANNUAL_DAYS[1]:
        return "annual"
    return None


def extract_facts(companyfacts: dict) -> tuple[list[dict], list[dict]]:
    """Flatten companyfacts into selected canonical facts.

    Returns (facts, events). Each fact:
    {field, tag, value, start, end, duration_type, filed, accn, form, fy, fp,
     restated_values: [...]}
    """
    gaap: dict[str, Any] = companyfacts.get("facts", {}).get("us-gaap", {})
    facts: list[dict] = []
    events: list[dict] = []

    for field, spec in FIELD_MAP.items():
        # merge facts across ALL mapped tags: companies switch tags over time
        # (e.g. Revenues → RevenueFromContractWithCustomer...), so one tag per
        # field drops periods. Within a period, the highest-priority tag wins.
        entries: list[tuple[int, str, dict]] = []
        for rank, tag in enumerate(spec["tags"]):
            if tag not in gaap:
                continue
            for unit in units_for(field):
                for e in gaap[tag].get("units", {}).get(unit, []):
                    entries.append((rank, tag, e))
        if not entries:
            continue

        # group occurrences of the same economic fact across filings
        grouped: dict[tuple, list[tuple[int, str, dict]]] = {}
        for rank, tag, e in entries:
            if e.get("val") is None or not e.get("end") or not e.get("filed"):
                continue
            dur = _duration_type(e.get("start"), e["end"])
            if dur is None:
                continue
            if spec["kind"] == "instant" and dur != "instant":
                continue
            if spec["kind"] != "instant" and dur == "instant":
                continue
            grouped.setdefault((e.get("start"), e["end"], dur), []).append(
                (rank, tag, e))

        for (start, end, dur), tagged_occ in grouped.items():
            first_filed = min(e["filed"] for _, _, e in tagged_occ)
            best_rank = min(r for r, _, e in tagged_occ if e["filed"] == first_filed)
            occ = [e for r, _, e in tagged_occ if r == best_rank]
            chosen_tag = next(t for r, t, _ in tagged_occ if r == best_rank)
            occ.sort(key=lambda e: (e["filed"], e.get("accn", "")))
            first = occ[0]
            # per-share sanity guard: a per-share fact beyond ±$10,000 is a
            # filing/units defect — drop it and log, never store
            if spec["kind"] == "per_share" and abs(first["val"]) > 10_000:
                events.append({
                    "event": "CORRUPT_FACT_DROPPED", "field": field,
                    "period_end": end, "value": first["val"],
                    "detail": "per-share value beyond ±10,000 — units defect",
                })
                continue
            restated = [
                {"value": o["val"], "filed": o["filed"], "accn": o.get("accn"),
                 "form": o.get("form")}
                for o in occ[1:] if o["val"] != first["val"]
            ]
            if restated:
                events.append({
                    "event": "RESTATEMENT",
                    "field": field, "period_end": end,
                    "original_value": first["val"],
                    "original_filed": first["filed"],
                    "later_values": restated,
                })
            facts.append({
                "field": field, "tag": chosen_tag, "value": first["val"],
                "start": start, "end": end, "duration_type": dur,
                "filed": first["filed"], "accn": first.get("accn"),
                "form": first.get("form"), "fy": first.get("fy"),
                "fp": first.get("fp"),
            })
    return facts, events


def _fiscal_label(facts_for_end: list[dict]) -> tuple[int | None, str | None]:
    """Most common (fy, fp) among facts sharing a period end — companyfacts'
    fy/fp refer to the filing, so first-filed occurrences carry the right label."""
    votes: dict[tuple, int] = {}
    for f in facts_for_end:
        if f.get("fy") and f.get("fp"):
            key = (f["fy"], f["fp"])
            votes[key] = votes.get(key, 0) + 1
    if not votes:
        return None, None
    (fy, fp), _ = max(votes.items(), key=lambda kv: kv[1])
    return fy, fp


def _rescale_share_facts(facts: list[dict], companyfacts: dict,
                         events: list[dict]) -> None:
    """Some filers (MCD, T, TXN, DAL, LUV) file weighted-average share counts
    scaled in millions despite the 'shares' unit. Detect per fact by
    cross-checking against the dei cover-page count (a real share number)
    and rescale ONLY when the ratio confirms a 1e6 scaling — a deterministic
    reconciliation against a second real source, never a guess."""
    dei = extract_shares_outstanding(companyfacts)
    if not dei:
        return
    corrected_fields = set()
    for f in facts:
        if FIELD_MAP[f["field"]]["kind"] != "shares":
            continue
        known = [r for r in dei if r.get("filed") and r["filed"] <= f["filed"]
                 and r["as_of"] <= f["filed"]]
        reference = max(known, key=lambda r: (r["as_of"], r["filed"]))["shares"] if known else None
        v = f["value"]
        if reference and v and v < 1e5 and 1e5 <= reference / v <= 1e7:
            f["value"] = v * 1e6
            corrected_fields.add(f["field"])
    for field in sorted(corrected_fields):
        events.append({
            "event": "SHARE_SCALE_CORRECTED", "field": field,
            "detail": "filer reports share counts in millions; rescaled x1e6 "
                      "after reconciling against dei cover-page count",
        })


def build_periods(companyfacts: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (quarterly_periods, annual_periods, data_quality_events).

    A period dict: {period_end, period_start, fiscal_year, fiscal_period,
    duration_type, filed_at, available_at, form, accession_number, derived,
    fields: {field: value}, field_sources: {field: accn}}
    """
    facts, events = extract_facts(companyfacts)
    _rescale_share_facts(facts, companyfacts, events)

    def assemble(dur: str) -> dict[str, dict]:
        by_end: dict[str, dict] = {}
        for f in facts:
            if f["duration_type"] not in (dur, "instant"):
                continue
            if f["duration_type"] == "instant":
                continue  # instants attached below by matching end date
            p = by_end.setdefault(f["end"], {
                "period_end": f["end"], "period_start": f["start"],
                "duration_type": dur, "fields": {}, "field_sources": {},
                "filed_at": f["filed"], "form": f["form"],
                "accession_number": f["accn"], "derived": False,
                "_labels": [],
            })
            p["fields"][f["field"]] = f["value"]
            p["field_sources"][f["field"]] = f["accn"]
            p["filed_at"] = max(p["filed_at"], f["filed"])
            p["_labels"].append(f)
        return by_end

    quarters = assemble("quarter")
    annuals = assemble("annual")

    # ---- fill quarterly flows from YTD cumulatives (Q2 = 6mo − Q1, ...) ----
    cum: dict[tuple, dict] = {}
    for f in facts:
        if f["duration_type"] in CUMULATIVE_TYPES and f["field"] not in NON_ADDITIVE_FIELDS:
            cum[(f["field"], f["start"], f["end"])] = f
    q_ends = sorted(quarters)
    for i, end in enumerate(q_ends):
        if i == 0:
            continue
        p = quarters[end]
        prev_end = q_ends[i - 1]
        # only difference against the immediately preceding quarter
        if not (80 <= (_parse_date(end) - _parse_date(prev_end)).days <= 115):
            continue
        for field in FLOW_FIELDS:
            if field in NON_ADDITIVE_FIELDS or field in p["fields"]:
                continue
            for (cf, cs, ce), fact in cum.items():
                if cf != field or ce != end:
                    continue
                prior = cum.get((field, cs, prev_end))
                if prior is None:
                    continue
                p["fields"][field] = round(fact["value"] - prior["value"], 6)
                p["field_sources"][field] = fact["accn"]
                p["_labels"].append(fact)
                p["filed_at"] = max(p["filed_at"], fact["filed"], prior["filed"])
                break

    # attach balance-sheet instants to any period sharing the end date
    instants = [f for f in facts if f["duration_type"] == "instant"]
    for coll in (quarters, annuals):
        for end, p in coll.items():
            for f in instants:
                if f["end"] == end and f["field"] not in p["fields"]:
                    p["fields"][f["field"]] = f["value"]
                    p["field_sources"][f["field"]] = f["accn"]
                    p["_labels"].append(f)
                    # A later-disclosed instant cannot travel back to the
                    # filing date of an earlier flow for the same period.
                    p["filed_at"] = max(p["filed_at"], f["filed"])

    for coll in (quarters, annuals):
        for p in coll.values():
            fy, fp = _fiscal_label(p.pop("_labels"))
            # Q4 facts are filed inside the 10-K, whose entries carry fp=FY
            if coll is quarters and fp == "FY":
                fp = "Q4"
            p["fiscal_year"], p["fiscal_period"] = fy, fp
            # SEC facts here have dates, not acceptance times. They are safe
            # for a closing-price information set only from the next day.
            p["available_at"] = (date.fromisoformat(p["filed_at"]) + timedelta(days=1)).isoformat()

    # ---- derive Q4 from FY - (Q1+Q2+Q3) for additive flow fields ----
    q_list = sorted(quarters.values(), key=lambda p: p["period_end"])
    for fy_end, ann in sorted(annuals.items()):
        if fy_end in quarters:
            continue  # Q4 reported explicitly
        start, end = ann.get("period_start"), ann["period_end"]
        if not start:
            continue
        inside = [q for q in q_list if start <= q["period_end"] < end]
        if len(inside) != 3:
            continue
        fields: dict[str, float] = {}
        sources: dict[str, str] = {}
        for field in FLOW_FIELDS:
            if field in NON_ADDITIVE_FIELDS:
                continue
            if field not in ann["fields"]:
                continue
            parts = [q["fields"].get(field) for q in inside]
            if any(v is None for v in parts):
                continue
            fields[field] = round(ann["fields"][field] - sum(parts), 6)
            sources[field] = ann["field_sources"].get(field, "")
        # balance-sheet instants at FY end belong to Q4 directly
        for field, val in ann["fields"].items():
            if FIELD_MAP[field]["kind"] == "instant":
                fields[field] = val
                sources[field] = ann["field_sources"].get(field, "")
        if not fields:
            continue
        q4_start = max(q["period_end"] for q in inside)
        quarters[end] = {
            "period_end": end, "period_start": q4_start,
            "duration_type": "quarter", "fields": fields,
            "field_sources": sources,
            "filed_at": max([ann["filed_at"]] + [q["filed_at"] for q in inside]),
            "available_at": max([ann["available_at"]] + [q["available_at"] for q in inside]),
            "form": ann["form"],
            "accession_number": ann["accession_number"], "derived": True,
            "fiscal_year": ann["fiscal_year"], "fiscal_period": "Q4",
        }

    quarterly = sorted(quarters.values(), key=lambda p: p["period_end"])
    annual = sorted(annuals.values(), key=lambda p: p["period_end"])

    # derived conveniences: free cash flow; gross profit when untagged
    # (Alphabet/Meta/Amazon report cost of revenue but no GrossProfit tag)
    for p in quarterly + annual:
        ocf = p["fields"].get("operating_cash_flow")
        capex = p["fields"].get("capital_expenditures")
        if ocf is not None and capex is not None:
            p["fields"]["free_cash_flow"] = ocf - capex
        if "gross_profit" not in p["fields"]:
            rev = p["fields"].get("revenue")
            cor = p["fields"].get("cost_of_revenue")
            if rev is not None and cor is not None:
                p["fields"]["gross_profit"] = rev - cor
                p["field_sources"]["gross_profit"] = \
                    p["field_sources"].get("revenue", "")

    return quarterly, annual, events


def extract_shares_outstanding(companyfacts: dict) -> list[dict]:
    """dei:EntityCommonStockSharesOutstanding — cover-page share counts,
    used only after the filing becomes available, on the matching share basis."""
    dei = companyfacts.get("facts", {}).get("dei", {})
    entries = (dei.get("EntityCommonStockSharesOutstanding", {})
               .get("units", {}).get("shares", []))
    out, seen = [], set()
    for e in sorted(entries, key=lambda e: e.get("filed") or "9999-12-31"):
        key = (e.get("end"), e.get("val"))
        if key in seen or e.get("val") is None:
            continue
        seen.add(key)
        out.append({"as_of": e["end"], "shares": e["val"],
                    "filed": e.get("filed"), "accn": e.get("accn"),
                    "available_at": (date.fromisoformat(e["filed"]) + timedelta(days=1)).isoformat() if e.get("filed") else None})
    out.sort(key=lambda r: (r["as_of"], r["filed"] or ""))
    return out
