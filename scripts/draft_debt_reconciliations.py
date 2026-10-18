"""Draft, then promote, reviewed debt reconciliations for withheld issuers.

  draft    For each covered issuer whose total debt is withheld, normalize its
           SEC companyfacts exactly as the pipeline does, fetch the pinned
           filing, and propose debt components from its face balance sheet.
           Issuers that do not tag total liabilities or cash also get a
           proposal for that field (face lines; liabilities must close the
           face identity). Writes a review packet (JSON + Markdown).
  promote  Append approved drafts to normalization/reviewed_balance_sheets.json
           after re-fetching the filing (hash must match) and re-normalizing
           (financial_integrity must accept the entry). Promotion is the
           review decision; DRAFT_NEEDS_REVIEW drafts need --accept-flags.
           --kind balance promotes the liabilities/cash proposals into
           normalization/reviewed_balance_fields.json instead.

Read-only against production; no database access, no trades.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from stock_machine import debt_reconciliation as dr  # noqa: E402
from stock_machine import financial_integrity as fi  # noqa: E402
from stock_machine.config import SEC_USER_AGENT  # noqa: E402
from stock_machine.normalization.financial_periods import build_periods  # noqa: E402

EVIDENCE = ROOT / "docs/evidence/latest-filing-balance-reconciliation-2026-09-16.json"
REVIEWED = ROOT / "stock_machine/normalization/reviewed_balance_sheets.json"
REVIEWED_FIELDS = ROOT / "stock_machine/normalization/reviewed_balance_fields.json"


class Sec:
    def __init__(self, cache: Path | None):
        self.client = httpx.Client(timeout=90, follow_redirects=True,
                                   headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"})
        self.cache = cache

    def get(self, url: str, name: str) -> bytes:
        path = self.cache / name if self.cache else None
        if path and path.exists():
            return path.read_bytes()
        import time
        time.sleep(0.15)
        response = self.client.get(url)
        response.raise_for_status()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(response.content)
        return response.content


def latest_quarter(sec: Sec, ticker: str, cik: str) -> dict | None:
    raw = sec.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", f"{ticker}.companyfacts.json")
    quarterly, _, _ = build_periods(json.loads(raw))
    return quarterly[-1] if quarterly else None


def filing_url(sec: Sec, ticker: str, cik: str, accession: str) -> str | None:
    submissions = json.loads(sec.get(f"https://data.sec.gov/submissions/CIK{cik}.json", f"{ticker}.submissions.json"))
    recent = submissions.get("filings", {}).get("recent", {})
    for accn, doc in zip(recent.get("accessionNumber", []), recent.get("primaryDocument", [])):
        if accn == accession:
            return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{doc}"
    return None


BALANCE_FIELDS = {"total_liabilities": "MISSING_BALANCE_FIELD:total_liabilities",
                  "cash_and_equivalents": "CASH_AND_EQUIVALENTS_MISSING_OR_INVALID"}


def balance_draft(facts, period, accession, field) -> dict:
    proposal = (dr.propose_liabilities if field == "total_liabilities" else dr.propose_cash)(facts)
    bound, flags = dr.balance_bindings(facts, period, accession, field)
    status = proposal["status"]
    if flags and status == dr.DRAFT_CLEAN:
        status = dr.DRAFT_NEEDS_REVIEW
    return {"field": field, **proposal, "status": status, "binding_fields": bound, "binding_flags": flags}


def draft_one(sec: Sec, ticker: str, cik: str) -> dict:
    out = {"ticker": ticker, "cik": cik, "drafted_by": dr.VERSION}
    period = latest_quarter(sec, ticker, cik)
    if not period:
        return {**out, "status": "NO_NORMALIZED_QUARTER"}
    check = fi.balance_sheet_check(period)
    out.update(period_end=period["period_end"], integrity_reasons=check["reasons"],
               debt_basis=check["debt"]["basis"])
    missing = [f for f, reason in BALANCE_FIELDS.items() if reason in check["reasons"]]
    debt_verified = check["debt"]["total_debt"] is not None
    if debt_verified and not missing:
        return {**out, "status": "DEBT_ALREADY_VERIFIED"}
    accession = (period.get("field_sources") or {}).get("total_assets") or period.get("accession_number")
    url = filing_url(sec, ticker, cik, accession) if accession else None
    if not url:
        return {**out, "accession_number": accession, "status": "FILING_NOT_FOUND"}
    raw = sec.get(url, f"{ticker}.{accession}.htm")
    text = raw.decode("utf-8", "replace")
    facts = dr.consolidated_facts(text, cik=cik, period_end=period["period_end"])
    out.update(accession_number=accession, source_url=url, source_sha256=hashlib.sha256(raw).hexdigest(),
               balance_fields=[balance_draft(facts, period, accession, f) for f in missing])
    if debt_verified:
        return {**out, "status": "DEBT_ALREADY_VERIFIED"}
    dimensional = dr.consolidated_facts(text, cik=cik, period_end=period["period_end"], dimensional=True)
    proposal = dr.propose(facts, dimensional)
    if proposal["flags"] == ["NO_DEBT_LINES_ON_FACE_BALANCE_SHEET"]:
        proposal = dr.propose_zero_debt(facts, dimensional)
    bound, binding_flags = dr.bindings(facts, period, accession)
    if not bound:
        binding_flags.append("NO_SAME_ACCESSION_BINDING")
    status = proposal["status"]
    if status == dr.DRAFT_CLEAN and "NO_SAME_ACCESSION_BINDING" in binding_flags:
        status = dr.DRAFT_NEEDS_REVIEW
    other = [r for r in check["reasons"] if r != "TOTAL_DEBT_NOT_SOURCE_RECONCILED"]
    return {**out, "status": status, "accession_number": accession, "source_url": url,
            "source_sha256": hashlib.sha256(raw).hexdigest(), "proposal": proposal,
            "binding_fields": bound, "binding_flags": binding_flags,
            "still_withheld_after_debt_for": other}


def markdown(drafts: list[dict]) -> str:
    def money(v):
        return "—" if v is None else f"${v / 1e9:,.3f}B"

    counts = {}
    for d in drafts:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    lines = ["# Debt reconciliation drafts", "",
             "Drafts only: nothing here affects the gate until promoted with "
             "`scripts/draft_debt_reconciliations.py promote`.", "",
             "Status counts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())), ""]
    for d in sorted(drafts, key=lambda d: (d["status"], d["ticker"])):
        lines.append(f"## {d['ticker']} — {d['status']}")
        for b in d.get("balance_fields") or []:
            lines.append(f"Missing **{b['field']}** — {b['status']}: proposed {money(b['value'])}"
                         + (f" (identity residual {b['identity_residual']:,}, bound {b['rounding_bound']:,})"
                            if "identity_residual" in b else ""))
            lines += [f"  - {c['label'] or '(no row label)'} — `{c['tag']}` {money(c['value'])}" for c in b["components"]]
            if b["flags"] + b["binding_flags"]:
                lines.append("  Flags: " + ", ".join(f"`{f}`" for f in b["flags"] + b["binding_flags"]))
        if "proposal" not in d:
            lines += [f"Period {d.get('period_end', '—')}; reasons: {', '.join(d.get('integrity_reasons') or []) or '—'}", ""]
            continue
        p = d["proposal"]
        lines += [f"[Filing]({d['source_url']}) · period {d['period_end']} · accession {d['accession_number']}",
                  f"Proposed total debt **{money(p['total_debt'])}**", ""]
        lines += [f"- {c['label'] or '(no row label)'} — `{c['tag']}` {money(c['value'])} ({c['element_id']})"
                  for c in p["components"]]
        if p.get("dimensional_face_debt"):
            lines.append("Face debt in dimensional contexts (not summed): " + ", ".join(
                f"{c['label'] or c['tag']} {money(c['value'])}" for c in p["dimensional_face_debt"]))
        if p["corroborated_by"]:
            lines.append("Corroborated by: " + ", ".join(f"`{c['tag']}` {money(c['value'])}" for c in p["corroborated_by"]))
        elif p["note_totals_seen"]:
            lines.append("Note totals seen (not equal): " + ", ".join(
                f"`{c['tag']}` {money(c['value'])}" for c in p["note_totals_seen"][:4]))
        flags = p["flags"] + d["binding_flags"]
        if flags:
            lines.append("Flags: " + ", ".join(f"`{f}`" for f in flags))
        if d["still_withheld_after_debt_for"]:
            lines.append("Still withheld after debt for: " + ", ".join(d["still_withheld_after_debt_for"]))
        lines.append("")
    return "\n".join(lines)


def promote_balance(args, sec: Sec) -> int:
    packet = json.loads(Path(args.drafts).read_text())
    by_ticker = {d["ticker"]: d for d in packet["drafts"]}
    reviewed = json.loads(REVIEWED_FIELDS.read_text())
    existing = {(r["cik"], r["period_end"], r["field"]) for r in reviewed}
    added = []
    for ticker in [t.strip().upper() for t in args.tickers.split(",") if t.strip()]:
        d = by_ticker.get(ticker) or {}
        drafts = [b for b in d.get("balance_fields") or [] if b["status"] in (dr.DRAFT_CLEAN, dr.DRAFT_NEEDS_REVIEW)]
        if not drafts:
            raise SystemExit(f"{ticker}: no promotable balance field draft")
        raw = sec.get(d["source_url"], f"{ticker}.{d['accession_number']}.htm")
        if hashlib.sha256(raw).hexdigest() != d["source_sha256"]:
            raise SystemExit(f"{ticker}: SOURCE_CONTENT_CHANGED")
        period = latest_quarter(sec, ticker, d["cik"])
        if period["period_end"] != d["period_end"]:
            raise SystemExit(f"{ticker}: a newer quarter has been normalized; redraft")
        for b in drafts:
            if b["status"] == dr.DRAFT_NEEDS_REVIEW and not args.accept_flags:
                raise SystemExit(f"{ticker} {b['field']}: draft has flags {b['flags'] + b['binding_flags']}; "
                                 "re-run with --accept-flags after reviewing them")
            if (d["cik"], d["period_end"], b["field"]) in existing:
                raise SystemExit(f"{ticker}: a reviewed {b['field']} for {d['period_end']} already exists")
            entry = dr.reviewed_field_entry(
                d, b, b["field"], b["binding_fields"], reviewed_on=args.reviewed_on or date.today().isoformat(),
                note=args.note or "Read from the face balance sheet of this filing; bound to same-accession "
                "normalized totals.")
            if fi.validate_reviewed_field(entry, period["fields"], period["field_sources"]) is None:
                raise SystemExit(f"{ticker} {b['field']}: financial_integrity rejects the entry")
            added.append(entry)
    REVIEWED_FIELDS.write_text(json.dumps(reviewed + added, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"promoted": [(e["ticker"], e["field"]) for e in added]}))
    return 0


def promote(args, sec: Sec) -> int:
    packet = json.loads(Path(args.drafts).read_text())
    by_ticker = {d["ticker"]: d for d in packet["drafts"]}
    reviewed = json.loads(REVIEWED.read_text())
    existing = {(r["cik"], r["period_end"]) for r in reviewed}
    added = []
    for ticker in [t.strip().upper() for t in args.tickers.split(",") if t.strip()]:
        d = by_ticker.get(ticker)
        if not d or d["status"] not in (dr.DRAFT_CLEAN, dr.DRAFT_NEEDS_REVIEW):
            raise SystemExit(f"{ticker}: no promotable draft")
        if d["status"] == dr.DRAFT_NEEDS_REVIEW and not args.accept_flags:
            raise SystemExit(f"{ticker}: draft has flags {d['proposal']['flags'] + d['binding_flags']}; "
                             "re-run with --accept-flags after reviewing them")
        if (d["cik"], d["period_end"]) in existing:
            raise SystemExit(f"{ticker}: a reviewed entry for {d['period_end']} already exists")
        raw = sec.get(d["source_url"], f"{ticker}.{d['accession_number']}.htm")
        if hashlib.sha256(raw).hexdigest() != d["source_sha256"]:
            raise SystemExit(f"{ticker}: SOURCE_CONTENT_CHANGED")
        entry = dr.reviewed_entry(d, reviewed_on=args.reviewed_on or date.today().isoformat(),
                                  note=args.note or "Debt lines read from the face balance sheet of this filing; "
                                  "bound to same-accession normalized balance sheet totals.")
        period = latest_quarter(sec, ticker, d["cik"])
        total, _ = fi.validate_reviewed_debt(entry, period["fields"], period["field_sources"])
        if period["period_end"] != d["period_end"] or total is None:
            raise SystemExit(f"{ticker}: financial_integrity rejects the entry against current normalization")
        added.append(entry)
    REVIEWED.write_text(json.dumps(reviewed + added, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"promoted": [e["ticker"] for e in added]}))
    return 0


def promote_custom(args, sec: Sec) -> int:
    """Owner-selected face debt lines, e.g. one segment of a dimensional split.

    Every element must be a debt fact at the latest normalized period end, for
    this entity, in USD, on the face balance sheet. The entry is accepted only
    if financial_integrity validates it against fresh normalization.
    """
    ticker = args.ticker.upper()
    row = next(r for r in json.loads(EVIDENCE.read_text())["rows"] if r["ticker"] == ticker)
    cik = row["cik"]
    period = latest_quarter(sec, ticker, cik)
    accession = period["field_sources"].get("total_assets")
    url = filing_url(sec, ticker, cik, accession)
    raw = sec.get(url, f"{ticker}.{accession}.htm")
    text = raw.decode("utf-8", "replace")
    plain = dr.consolidated_facts(text, cik=cik, period_end=period["period_end"])
    every = plain + dr.consolidated_facts(text, cik=cik, period_end=period["period_end"], dimensional=True)
    tables = dr.face_tables(plain)
    by_id = {f["element_id"]: f for f in every}
    chosen = []
    for element in [e.strip() for e in args.element_ids.split(",") if e.strip()]:
        f = by_id.get(element)
        if not f or f["table"] not in tables or not dr.is_debt_tag(f["tag"]):
            raise SystemExit(f"{ticker}: {element} is not a face balance sheet debt fact for {period['period_end']}")
        chosen.append(f)
    bound, binding_flags = dr.bindings(plain, period, accession)
    proposal = {"components": [dr._public(f) for f in chosen], "flags": ["OWNER_SELECTED_FACE_LINES"],
                "total_debt": int(sum(f["value"] for f in chosen)), "method": "OWNER_SELECTED_FACE_LINES"}
    draft = {"ticker": ticker, "cik": cik, "period_end": period["period_end"], "accession_number": accession,
             "source_url": url, "source_sha256": hashlib.sha256(raw).hexdigest(),
             "proposal": proposal, "binding_fields": bound}
    entry = dr.reviewed_entry(draft, reviewed_on=args.reviewed_on or date.today().isoformat(), note=args.note)
    entry["selected_lines"] = [{"element_id": f["element_id"], "tag": f["tag"], "label": f["label"],
                                "members": f["members"], "value": int(f["value"])} for f in chosen]
    total, _ = fi.validate_reviewed_debt(entry, period["fields"], period["field_sources"])
    if total is None:
        raise SystemExit(f"{ticker}: financial_integrity rejects the entry ({binding_flags})")
    reviewed = json.loads(REVIEWED.read_text())
    if any(r["cik"] == cik and r["period_end"] == period["period_end"] for r in reviewed):
        raise SystemExit(f"{ticker}: a reviewed entry for {period['period_end']} already exists")
    REVIEWED.write_text(json.dumps(reviewed + [entry], indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"promoted": ticker, "total_debt": total, "lines": entry["selected_lines"]}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("draft")
    d.add_argument("--tickers", help="comma-separated; default all covered issuers")
    d.add_argument("--out", default=str(ROOT / "docs/evidence/debt-reconciliation-drafts"))
    p = sub.add_parser("promote")
    p.add_argument("--drafts", required=True)
    p.add_argument("--tickers", required=True)
    p.add_argument("--accept-flags", action="store_true")
    p.add_argument("--kind", choices=("debt", "balance"), default="debt",
                   help="debt reviews, or missing total liabilities / cash")
    p.add_argument("--reviewed-on")
    p.add_argument("--note")
    c = sub.add_parser("custom", help="promote owner-selected face debt lines by element id")
    c.add_argument("--ticker", required=True)
    c.add_argument("--element-ids", required=True)
    c.add_argument("--note", required=True)
    c.add_argument("--reviewed-on")
    for s in (d, p, c):
        s.add_argument("--cache-dir", type=Path, help="reuse fetched SEC responses")
    args = parser.parse_args()
    if "@" not in SEC_USER_AGENT:
        # www.sec.gov answers 403 without contact details (SEC fair-access policy).
        raise SystemExit("Set SEC_USER_AGENT to 'Name contact@your-domain' before fetching filings")
    sec = Sec(args.cache_dir)
    if args.command == "custom":
        return promote_custom(args, sec)
    if args.command == "promote":
        return promote_balance(args, sec) if args.kind == "balance" else promote(args, sec)
    rows = json.loads(EVIDENCE.read_text())["rows"]
    wanted = {t.strip().upper() for t in args.tickers.split(",")} if args.tickers else None
    drafts = []
    for row in rows:
        if wanted and row["ticker"] not in wanted:
            continue
        try:
            drafts.append(draft_one(sec, row["ticker"], row["cik"]))
        except Exception as exc:  # one issuer never stops the packet
            drafts.append({"ticker": row["ticker"], "cik": row["cik"], "status": "ERROR", "error": repr(exc)[:300]})
        print(drafts[-1]["ticker"], drafts[-1]["status"], file=sys.stderr)
    packet = {"schema_version": dr.VERSION, "drafted_on": date.today().isoformat(),
              "gate_effect": "NONE_UNTIL_PROMOTED", "drafts": drafts}
    out = Path(args.out)
    out.with_suffix(".json").write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
    out.with_suffix(".md").write_text(markdown(drafts) + "\n", encoding="utf-8")
    print(json.dumps({k: sum(1 for x in drafts if x["status"] == k) for k in {x["status"] for x in drafts}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
