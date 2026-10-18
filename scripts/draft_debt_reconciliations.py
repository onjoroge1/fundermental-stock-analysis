"""Draft, then promote, reviewed debt reconciliations for withheld issuers.

  draft    For each covered issuer whose total debt is withheld, normalize its
           SEC companyfacts exactly as the pipeline does, fetch the pinned
           filing, and propose debt components from its face balance sheet.
           Writes a review packet (JSON + Markdown). Changes nothing else.
  promote  Append approved drafts to normalization/reviewed_balance_sheets.json
           after re-fetching the filing (hash must match) and re-normalizing
           (financial_integrity must accept the entry). Promotion is the
           review decision; DRAFT_NEEDS_REVIEW drafts need --accept-flags.

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


def draft_one(sec: Sec, ticker: str, cik: str) -> dict:
    out = {"ticker": ticker, "cik": cik, "drafted_by": dr.VERSION}
    period = latest_quarter(sec, ticker, cik)
    if not period:
        return {**out, "status": "NO_NORMALIZED_QUARTER"}
    check = fi.balance_sheet_check(period)
    out.update(period_end=period["period_end"], integrity_reasons=check["reasons"],
               debt_basis=check["debt"]["basis"])
    if check["debt"]["total_debt"] is not None:
        return {**out, "status": "DEBT_ALREADY_VERIFIED"}
    accession = (period.get("field_sources") or {}).get("total_assets") or period.get("accession_number")
    url = filing_url(sec, ticker, cik, accession) if accession else None
    if not url:
        return {**out, "accession_number": accession, "status": "FILING_NOT_FOUND"}
    raw = sec.get(url, f"{ticker}.{accession}.htm")
    facts = dr.consolidated_facts(raw.decode("utf-8", "replace"), cik=cik, period_end=period["period_end"])
    proposal = dr.propose(facts)
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
        if "proposal" not in d:
            lines += [f"Period {d.get('period_end', '—')}; reasons: {', '.join(d.get('integrity_reasons') or []) or '—'}", ""]
            continue
        p = d["proposal"]
        lines += [f"[Filing]({d['source_url']}) · period {d['period_end']} · accession {d['accession_number']}",
                  f"Proposed total debt **{money(p['total_debt'])}**", ""]
        lines += [f"- {c['label'] or '(no row label)'} — `{c['tag']}` {money(c['value'])} ({c['element_id']})"
                  for c in p["components"]]
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
    p.add_argument("--reviewed-on")
    p.add_argument("--note")
    for s in (d, p):
        s.add_argument("--cache-dir", type=Path, help="reuse fetched SEC responses")
    args = parser.parse_args()
    if "@" not in SEC_USER_AGENT:
        # www.sec.gov answers 403 without contact details (SEC fair-access policy).
        raise SystemExit("Set SEC_USER_AGENT to 'Name contact@your-domain' before fetching filings")
    sec = Sec(args.cache_dir)
    if args.command == "promote":
        return promote(args, sec)
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
