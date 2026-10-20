"""Which reviewed debt and balance fields a newer filing has superseded.

Each entry in normalization/reviewed_balance_sheets.json (total debt) and reviewed_balance_fields.json (total
liabilities, cash) is pinned to one filing: (cik, period_end, accession_number). When the issuer files a newer
quarter the review stops applying and the gate withholds the period with the same generic reason as an issuer
that was never reviewed. This module names the difference, per kind:

  REVIEW_PINNED_TO_SUPERSEDED_FILING:<kind>  the newest review of that kind is for an earlier period: re-review
  REVIEW_NOT_BINDING_FOR_PERIOD:<kind>       a review for this very period exists but no longer validates
  REVIEW_AWAITING_NORMALIZATION:<kind>       a review for this period validates but the nightly normalization has
                                             not applied it yet (it clears on the next refresh)

Diagnostic only: a finding is added only next to the generic reason it explains, so it never changes a status, a
number or a blocker; the generic reasons stay, because the drafter and the tests key on them. `scan` lists every
reviewed issuer against its latest stored quarter and filing for the health watchdog and the owner page. It reads
the database only (no SEC call) and is imported by no module that decides, learns or trades.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path

from . import financial_integrity as fi

VERSION = "review-coverage.v1"
SUPERSEDED = "REVIEW_PINNED_TO_SUPERSEDED_FILING"
NOT_BINDING = "REVIEW_NOT_BINDING_FOR_PERIOD"
AWAITING = "REVIEW_AWAITING_NORMALIZATION"
# Review kind -> the generic gate reason showing that kind withholds the period.
KINDS = {
    "total_debt": "TOTAL_DEBT_NOT_SOURCE_RECONCILED",
    "total_liabilities": "MISSING_BALANCE_FIELD:total_liabilities",
    "cash_and_equivalents": "CASH_AND_EQUIVALENTS_MISSING_OR_INVALID",
}
FIELD_KINDS = ("total_liabilities", "cash_and_equivalents")
REVIEW_FILES = {"total_debt": "reviewed_balance_sheets.json", "total_liabilities": "reviewed_balance_fields.json",
                "cash_and_equivalents": "reviewed_balance_fields.json"}
OWNER_SELECTED = "OWNER_SELECTED_FACE_LINES"


def _cik(value) -> str:
    return str(value or "").strip().zfill(10)


def reviews(cik, *, as_of: str | None = None) -> dict[str, list[dict]]:
    """The issuer's reconciled reviews by kind; with `as_of`, only those reviewed by then."""
    key = _cik(cik)
    out: dict[str, list[dict]] = {kind: [] for kind in KINDS}
    rows = [("total_debt", row) for row in fi.reviewed_debt_sources()]
    rows += [(row.get("field"), row) for row in fi.reviewed_balance_fields()]
    for kind, row in rows:
        if (kind in out and _cik(row.get("cik")) == key and row.get("status") == "SOURCE_RECONCILED"
                and row.get("period_end") and (as_of is None or (row.get("reviewed_on") or "") <= as_of[:10])):
            out[kind].append(row)
    return out


def _pending_fields(period: dict, same: dict[str, dict]) -> tuple[dict, dict]:
    """The period's fields as the next normalization would leave them: field reviews first, as in build_periods."""
    fields, sources = dict(period.get("fields") or {}), dict(period.get("field_sources") or {})
    for kind in FIELD_KINDS:
        review = same.get(kind)
        value = fi.validate_reviewed_field(review, fields, sources) if review else None
        if value is not None:
            fields[kind], sources[kind] = value, review["accession_number"]
    return fields, sources


def coverage(cik, period: dict | None, reasons: list[str], *, as_of: str | None = None) -> list[dict]:
    """One finding per review kind that withholds `period` although the issuer has a review of that kind."""
    end = (period or {}).get("period_end")
    if not end:
        return []
    by_kind = reviews(cik, as_of=as_of)
    same = {kind: next((r for r in rows if r["period_end"] == end), None) for kind, rows in by_kind.items()}
    same = {kind: row for kind, row in same.items() if row}
    pending = _pending_fields(period, same) if same else None
    sources = period.get("field_sources") or {}
    own = sources.get("total_assets") or period.get("accession_number")
    found = []
    for kind, generic in KINDS.items():
        rows = by_kind[kind]
        if generic not in reasons or not rows:
            continue
        review = same.get(kind)
        if review:
            if kind == "total_debt":
                ok = fi.validate_reviewed_debt(review, *pending)[0] is not None
            else:
                ok = pending[0].get(kind) is not None and pending[1].get(kind) == review["accession_number"]
            # normalization applies a review only on the exact 10-digit cik (build_periods)
            code = AWAITING if ok and review.get("cik") == _cik(cik) else NOT_BINDING
        elif own and any(r.get("accession_number") == own for r in rows):
            # cites this very filing under another period_end (a 52/53-week date): it can never bind
            review, code = next(r for r in rows if r.get("accession_number") == own), NOT_BINDING
        else:
            review = max(rows, key=lambda r: r["period_end"])
            if review["period_end"] > end:
                continue  # reviewed only for later periods: a historical period, never reviewed
            code = SUPERSEDED
        found.append({
            "reason": f"{code}:{kind}", "state": code, "kind": kind, "review_file": REVIEW_FILES[kind],
            "reviewed_period_end": review["period_end"], "reviewed_accession": review.get("accession_number"),
            "review_source_id": review.get("source_id"), "review_method": review.get("method"),
            "reviewed_on": review.get("reviewed_on"),
            "period_end": end, "period_accession": sources.get("total_assets") or period.get("accession_number"),
            "period_form": period.get("form"), "period_filed_at": period.get("filed_at"),
            "period_derived": bool(period.get("derived")),
        })
    return found


def annotate(integrity: dict, cik, period: dict | None, *, as_of: str | None = None) -> dict:
    """Add the findings next to the generic reasons, in place (the reasons list is shared with the
    withheld scores), and their details under `review_coverage`. Status and numbers are untouched."""
    reasons = integrity.setdefault("reasons", [])
    found = coverage(cik, period, reasons, as_of=as_of)
    for finding in found:
        if finding["reason"] not in reasons:
            reasons.append(finding["reason"])
    integrity["review_coverage"] = found
    return integrity


# ---- nightly scan --------------------------------------------------------------

SCAN_SQL = """
WITH q AS (
  SELECT DISTINCT ON (ticker) ticker, period_end::text AS period_end, fiscal_year, fiscal_period,
         filed_at::text AS filed_at, available_at::text AS available_at, form, accession_number, derived,
         fields, field_sources
  FROM financial_periods
  WHERE duration_type = 'quarter' AND available_at <= %(today)s::date AND period_end <= %(today)s::date
  ORDER BY ticker, period_end DESC),
f AS (
  SELECT DISTINCT ON (ticker) ticker, accession_number, form, filed_at::text AS filed_at, report_date::text AS report_date
  FROM filings
  WHERE form IN ('10-Q', '10-K') AND report_date IS NOT NULL AND filed_at < %(today)s::date
    AND report_date <= %(today)s::date
  ORDER BY ticker, report_date DESC, filed_at DESC),
s AS (
  SELECT DISTINCT ON (ticker) ticker, last_checked_at
  FROM dataset_snapshots WHERE dataset = 'fundamentals'
  ORDER BY ticker, last_checked_at DESC)
SELECT c.ticker, c.cik,
       q.period_end, q.fiscal_year, q.fiscal_period, q.filed_at, q.available_at, q.form, q.accession_number,
       q.derived, q.fields, q.field_sources,
       f.accession_number, f.form, f.filed_at, f.report_date, s.last_checked_at
FROM companies c
LEFT JOIN q ON q.ticker = c.ticker
LEFT JOIN f ON f.ticker = c.ticker
LEFT JOIN s ON s.ticker = c.ticker
WHERE c.cik = ANY(%(ciks)s)
ORDER BY c.ticker
"""
# The nightly strategy-forward run records whether SEC_USER_AGENT carries a contact address (item 47).
SEC_CONTACT_SQL = """SELECT details->>'sec_contact_configured' FROM operator_audit
WHERE event = 'STRATEGY_FORWARD_STAGE_FINISHED' ORDER BY id DESC LIMIT 1"""
PERIOD_COLUMNS = ("period_end", "fiscal_year", "fiscal_period", "filed_at", "available_at", "form",
                  "accession_number", "derived", "fields", "field_sources")
FILING_COLUMNS = ("accession_number", "form", "filed_at", "report_date")
# Issuer states, worst first. DUE and NOT_BINDING need the owner; the rest are information.
STATES = ("DUE", "NOT_BINDING", "AWAITING_NORMALIZATION", "REVIEW_AHEAD_OF_DATA", "NO_QUARTER", "NOT_IN_DATABASE",
          "NO_LONGER_NEEDED", "CURRENT", "NOT_REVIEWED")


def _file_digest(name: str) -> dict:
    raw = (Path(fi.__file__).parent / "normalization" / name).read_bytes()
    return {"sha256": hashlib.sha256(raw).hexdigest()}


def _review_summary(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    newest = max(rows, key=lambda r: r["period_end"])
    return {"period_end": newest["period_end"], "accession_number": newest.get("accession_number"),
            "method": newest.get("method"), "reviewed_on": newest.get("reviewed_on"), "reviews": len(rows)}


def _flags(kinds: set[str], debt: dict) -> dict:
    """Which drafter promote each kind needs, given the method of the issuer's newest debt review."""
    method = debt.get("method")
    return {"balance": bool(kinds & set(FIELD_KINDS)),
            "debt": "total_debt" in kinds and method not in (OWNER_SELECTED, fi.ZERO_DEBT_METHOD),
            "zero_debt": "total_debt" in kinds and method == fi.ZERO_DEBT_METHOD,
            "custom": "total_debt" in kinds and method == OWNER_SELECTED}


def _promote_steps(flags: dict) -> list[str]:
    steps = ["promote --kind balance"] if flags["balance"] else []
    if flags["custom"]:
        steps.append("custom with the element ids of the new filing's face debt facts that match "
                     "previous_selection (same tags and members; ids change every filing)")
    elif flags["zero_debt"]:
        steps.append("promote --accept-flags if the draft is ZERO_DEBT_PROPOSED (NO_PROPOSAL means the filing "
                     "now shows debt: review it like any other)")
    elif flags["debt"]:
        steps.append("promote")
    return steps


def classify(cik, ticker: str, period: dict | None, filing: dict | None, *, today: date,
             last_ingested_at: str | None = None) -> dict:
    """One reviewed issuer against its latest stored quarter (`period`) and latest original 10-Q/10-K (`filing`)."""
    by_kind = reviews(cik)
    reviewed = {kind: _review_summary(rows) for kind, rows in by_kind.items() if rows}
    newest = max((r["period_end"] for r in reviewed.values()), default=None)
    row = {"ticker": ticker, "cik": _cik(cik), "reviewed": reviewed, "last_ingested_at": last_ingested_at,
           "latest_filing": filing, "findings": []}
    if filing and filing.get("filed_at"):
        row["latest_filing"] = {**filing, "days_since_filed": (today - date.fromisoformat(filing["filed_at"][:10])).days}
    if period is None:
        return {**row, "state": "NO_QUARTER", "latest_period": None, "integrity": None,
                "newer_filing_not_normalized": bool(filing)}
    check = fi.balance_sheet_check(period)
    found = coverage(cik, period, check["reasons"])
    sources = period.get("field_sources") or {}
    row.update({
        "latest_period": {"period_end": period["period_end"], "fiscal_year": period.get("fiscal_year"),
                          "fiscal_period": period.get("fiscal_period"), "form": period.get("form"),
                          "filed_at": period.get("filed_at"), "available_at": period.get("available_at"),
                          "derived": bool(period.get("derived")),
                          "balance_accession": sources.get("total_assets") or period.get("accession_number")},
        "integrity": {"status": check["status"], "reasons": check["reasons"] + [f["reason"] for f in found],
                      "debt_basis": check["debt"]["basis"]},
        "findings": found,
        "newer_filing_not_normalized": bool(filing and filing["report_date"] > period["period_end"]),
    })
    states = {f["state"] for f in found}
    if newest is None:
        state = "NOT_REVIEWED"
    elif SUPERSEDED in states:
        state = "DUE"
    elif NOT_BINDING in states:
        state = "NOT_BINDING"
    elif AWAITING in states:
        state = "AWAITING_NORMALIZATION"
    elif newest > period["period_end"]:
        state = "REVIEW_AHEAD_OF_DATA"
    elif newest < period["period_end"]:
        state = "NO_LONGER_NEEDED"  # the newer period verifies without a review (e.g. the issuer now tags it)
    else:
        state = "CURRENT"
    row["state"] = state
    debt = max(by_kind["total_debt"], key=lambda r: r["period_end"]) if by_kind["total_debt"] else {}
    row["promote"] = _flags({f["kind"] for f in found if f["state"] == SUPERSEDED}, debt)
    row["repair_promote"] = _flags({f["kind"] for f in found if f["state"] == NOT_BINDING}, debt)
    broken = [f for f in found if f["state"] == NOT_BINDING]
    if row["promote"]["custom"] or row["repair_promote"]["custom"]:
        row["previous_selection"] = [{k: line.get(k) for k in ("tag", "members", "label")}
                                     for line in debt.get("selected_lines") or []]
    steps = []
    if broken:
        entries = "; ".join(f"the {f['kind']} entry for {f['reviewed_period_end']} ({f['reviewed_accession']}) in "
                            f"{f['review_file']}" for f in broken)
        steps.append(f"repair: remove {entries}, then draft and promote it again (promote refuses a period that "
                     "already has an entry)")
    if state == "DUE":
        steps.append("draft, then " + ", then ".join(_promote_steps(row["promote"])))
    if steps:
        row["next_step"] = "; ".join(steps)
    if state == "NO_LONGER_NEEDED" and debt.get("method") == OWNER_SELECTED:
        row["next_step"] = "check: debt now verifies without the owner's segment selection (consolidated basis?)"
    return row


def custom_command(stem: str) -> str:
    """The owner-selected re-review (F, GM, TMUS): the placeholders are filled from the packet and previous_selection."""
    return (f"python scripts/draft_debt_reconciliations.py custom --ticker <T> --element-ids <ids from {stem}.json "
            'matching previous_selection for that ticker> --note "<the owner decision, e.g. automotive-only debt>"')


def scan(conn, *, now: datetime | None = None) -> dict:
    """Every reviewed issuer against its latest stored quarter and filing. Read-only; no SEC call."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    ciks = sorted({_cik(r.get("cik")) for r in (*fi.reviewed_debt_sources(), *fi.reviewed_balance_fields())
                   if r.get("status") == "SOURCE_RECONCILED"})
    conn.execute("SET TRANSACTION READ ONLY")
    fetched = conn.execute(SCAN_SQL, {"today": today.isoformat(), "ciks": ciks}).fetchall()
    receipt = conn.execute(SEC_CONTACT_SQL).fetchone()
    conn.rollback()
    nightly_contact = {"true": True, "false": False}.get(receipt[0]) if receipt else None
    names = {}
    for r in (*fi.reviewed_debt_sources(), *fi.reviewed_balance_fields()):
        names.setdefault(_cik(r.get("cik")), r.get("ticker"))
    rows, seen = [], set()
    for record in fetched:
        ticker, cik = record[0], _cik(record[1])
        period = dict(zip(PERIOD_COLUMNS, record[2:12])) if record[2] else None
        filing = dict(zip(FILING_COLUMNS, record[12:16])) if record[12] else None
        checked = record[16]
        rows.append(classify(cik, ticker, period, filing, today=today,
                             last_ingested_at=checked.isoformat() if checked else None))
        seen.add(cik)
    for cik in ciks:
        if cik not in seen:
            rows.append({"ticker": names.get(cik), "cik": cik, "state": "NOT_IN_DATABASE", "findings": [],
                         "reviewed": {k: _review_summary(v) for k, v in reviews(cik).items() if v}})
    rows.sort(key=lambda r: (STATES.index(r["state"]), r["ticker"] or ""))
    due = [r for r in rows if r["state"] == "DUE"]
    broken = [r for r in rows if any(f["state"] == NOT_BINDING for f in r["findings"])]
    stem = f"docs/evidence/debt-reconciliation-drafts-{today.isoformat()}"
    tool = "python scripts/draft_debt_reconciliations.py"

    def commands(group, key):
        def listed(flag):
            return ",".join(r["ticker"] for r in group if r[key][flag])
        out = [f"{tool} promote --kind balance --drafts {stem}.json --tickers {listed('balance')}" if listed("balance") else None,
               f"{tool} promote --drafts {stem}.json --tickers {listed('debt')}" if listed("debt") else None,
               f"{tool} promote --accept-flags --drafts {stem}.json --tickers {listed('zero_debt')}"
               if listed("zero_debt") else None]
        return [c for c in out if c]
    owner_selected = [r["ticker"] for r in due + broken if r["promote"]["custom"] or r["repair_promote"]["custom"]]
    drafts = list(dict.fromkeys(r["ticker"] for r in due + broken))
    contact = ("SEC_USER_AGENT with a contact address: locally for every drafter command" +
               ("" if nightly_contact else ", and as the Actions variable so the nightly can read filings that "
                                           "companyfacts has not caught up with") + " (item 47)")
    return {
        "schema_version": VERSION, "generated_at": now.isoformat(timespec="seconds"),
        "review_files": {name: _file_digest(name) for name in sorted(set(REVIEW_FILES.values()))},
        "issuers": len(rows), "counts": {s: sum(r["state"] == s for r in rows) for s in STATES},
        "due": [{"ticker": r["ticker"], "period_end": r["latest_period"]["period_end"],
                 "form": r["latest_period"]["form"], "filed_at": r["latest_period"]["filed_at"],
                 "kinds": [f["kind"] for f in r["findings"] if f["state"] == SUPERSEDED],
                 "reviewed_period_end": max(f["reviewed_period_end"] for f in r["findings"] if f["state"] == SUPERSEDED)}
                for r in due],
        "not_binding": [{"ticker": r["ticker"], "period_end": r["latest_period"]["period_end"],
                         "kinds": [f["kind"] for f in r["findings"] if f["state"] == NOT_BINDING],
                         "next_step": r["next_step"]} for r in broken],
        "repair_promote_commands": commands(broken, "repair_promote"),
        "awaiting_normalization": [r["ticker"] for r in rows if r["state"] == "AWAITING_NORMALIZATION"],
        "filed_not_normalized": [{"ticker": r["ticker"], **{k: r["latest_filing"].get(k) for k in
                                                            ("form", "report_date", "filed_at", "days_since_filed")}}
                                 for r in rows if r.get("newer_filing_not_normalized") and r.get("latest_filing")],
        "draft_command": f"{tool} draft --tickers {','.join(drafts)} --out {stem}" if drafts else None,
        "promote_commands": commands(due, "promote"),
        "promote_note": ("read the .md packet first; drop any ticker whose draft you reject; --accept-flags only for "
                         "flags you have read; liabilities and cash (--kind balance) before debt") if due else None,
        "custom_tickers": owner_selected,
        "custom": [{"ticker": r["ticker"], "previous_selection": r.get("previous_selection") or []}
                   for r in due + broken if r["ticker"] in owner_selected],
        "custom_command": custom_command(stem) if owner_selected else None,
        "sec_contact": {"nightly": nightly_contact},
        "requires": contact if (due or broken or nightly_contact is False) else None,
        "rows": rows,
        "use": "INFORMATION_ONLY: lists re-reviews; never changes a gate, a decision or a trade",
    }
