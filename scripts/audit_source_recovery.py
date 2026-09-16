"""Reproduce the 54-name recovery audit from captured public source responses.

This is an offline evidence audit, not production ingestion or a historical
claim that a prospective forecast was persisted before its observation date.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

from stock_machine.bundle import _period_json, STATEMENT_FIELDS
from stock_machine.claim_evidence import audit_claims
from stock_machine.financial_integrity import balance_sheet_check
from stock_machine.normalization.financial_periods import build_periods
from stock_machine.research_contract import digest, evaluate
from stock_machine.research_cycle import build_brief


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--live-dir", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    now = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    if now.utcoffset() is None:
        parser.error("as-of must include a time zone")
    rows, pilot = [], []
    universe = json.loads((args.source_dir / "universe.json").read_text())
    for company in universe:
        ticker = company["ticker"]
        raw = json.loads((args.source_dir / f"{ticker}-companyfacts.json").read_text())
        sub = json.loads((args.source_dir / f"{ticker}-submissions.json").read_text())
        q, a, _ = build_periods(raw)
        q = [p for p in q if p["available_at"] <= now.date().isoformat()]
        a = [p for p in a if p["available_at"] <= now.date().isoformat()]
        latest = q[-1]
        balance = balance_sheet_check(latest)
        recent = sub["filings"]["recent"]
        filings = [{"form": recent["form"][i], "report_date": recent["reportDate"][i],
                    "filed_at": recent["filingDate"][i], "accession_number": accession,
                    "source_url": f"https://www.sec.gov/Archives/edgar/data/{int(company['cik'])}/{accession.replace('-', '')}/{recent['primaryDocument'][i]}"}
                   for i, accession in enumerate(recent["accessionNumber"])
                   if recent["form"][i] in ("10-Q", "10-K", "10-Q/A", "10-K/A")
                   and recent["filingDate"][i] < now.date().isoformat()]
        filing = max(filings, key=lambda f: (f["report_date"], f["filed_at"])) if filings else None
        balance["latest_financial_filing"] = filing
        if filing and filing["report_date"] > latest["period_end"]:
            balance["status"] = "WITHHELD"
            balance["reasons"].append("NEWER_FINANCIAL_FILING_NOT_NORMALIZED")
        live = json.loads((args.live_dir / f"{ticker}-bundle.json").read_text())
        report = json.loads((args.live_dir / f"{ticker}-report.json").read_text())
        bundle = {"company": live["company"], "market_snapshot": live["market_snapshot"],
                  "financial_history": {"quarterly_periods": [_period_json(p, STATEMENT_FIELDS) for p in q],
                                        "annual_periods": [_period_json(p, STATEMENT_FIELDS) for p in a]},
                  "data_quality": {"status": "PASS" if balance["status"] == "VERIFIED" else "WARN", "financial_integrity": balance},
                  "invalidation_breaches": []}
        claims = audit_claims(report, bundle)
        provenance = balance["debt"].pop("field_provenance")
        rows.append({"ticker": ticker, "cik": company["cik"], "source_companyfacts_sha256": digest(raw),
                     "source_submissions_sha256": digest(sub), "source_url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{company['cik']}.json",
                     "latest_balance": balance, "balance_fields": {k: v for k, v in latest["fields"].items() if k in STATEMENT_FIELDS["balance_sheet"]},
                     "field_provenance": {k: v for k, v in provenance.items() if k in STATEMENT_FIELDS["balance_sheet"]},
                     "legacy_report_as_of": report.get("as_of"), "legacy_report_sha256": digest(report), "claim_audit": claims})
        if ticker in ("AAPL", "MSFT", "UBER", "HIMS", "VZ"):
            brief = build_brief(bundle, report, now=now)
            from stock_machine.report_schema import validate_analysis_report
            validate_analysis_report(brief, source_bundle=bundle)
            pilot.append({"ticker": ticker, "brief": brief, "contract": evaluate(bundle, brief, None, now=now),
                          "exercise": "OFFLINE_PUBLIC_SOURCE_EXERCISE_NOT_PRODUCTION_CAPTURE"})
    result = {"audit_as_of": now.isoformat(), "expected": len(universe), "audited": len(rows),
              "financial_status": dict(Counter(r["latest_balance"]["status"] for r in rows)),
              "claims_total": sum(r["claim_audit"]["total"] for r in rows),
              "claims_verified": sum(r["claim_audit"]["verified"] for r in rows),
              "newer_filing_not_normalized": [r["ticker"] for r in rows if "NEWER_FINANCIAL_FILING_NOT_NORMALIZED" in r["latest_balance"]["reasons"]],
              "rows": rows, "pilot_exercise": pilot}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("rows", "pilot_exercise")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
