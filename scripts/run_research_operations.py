"""Explicit database operations; every source failure is retained and fails the job."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from stock_machine import db, research_store
from stock_machine.claim_evidence import audit_claims
from stock_machine.financial_integrity import balance_sheet_check
from stock_machine.research_contract import digest, read_inputs


def reconcile(*, refresh_sec=False):
    with db.connect() as conn:
        db.init_schema(conn)
        companies = db.list_companies(conn)
    rows = []
    for company in companies:
        ticker = company["ticker"]
        try:
            if refresh_sec:
                from stock_machine.ingestion.sec import fetch_companyfacts, fetch_submissions
                from stock_machine.normalization.financial_periods import build_periods
                from stock_machine.data_quality import assess_dataset
                raw = fetch_companyfacts(ticker, company["cik"])
                sub = fetch_submissions(ticker, company["cik"])
                quarters, annual, events = build_periods(raw)
                recent = sub.get("filings", {}).get("recent", {})
                filings = [{"accession_number": accession, "form": recent["form"][i],
                            "filed_at": recent["filingDate"][i] or None,
                            "report_date": recent["reportDate"][i] or None,
                            "primary_document": recent["primaryDocument"][i]}
                           for i, accession in enumerate(recent.get("accessionNumber", []))
                           if recent["form"][i] in ("10-Q", "10-K", "10-Q/A", "10-K/A", "8-K")]
                with db.connect() as conn:
                    # Keep the exact source even when normalization fails later.
                    original_raw = {k: v for k, v in raw.items() if k != "_source_provenance"}
                    research_store.save(conn, "RAW_SOURCE", digest(original_raw), original_raw, ticker)
                    db.replace_periods(conn, ticker, quarters, annual)
                    db.replace_filings(conn, ticker, filings)
                    snapshot = assess_dataset("fundamentals", quarters + annual)
                    snapshot["payload"] = quarters + annual
                    db.record_dataset_snapshots(conn, ticker, [snapshot])
                    filing_snapshot = assess_dataset("filings", filings)
                    filing_snapshot["payload"] = filings
                    db.record_dataset_snapshots(conn, ticker, [filing_snapshot])
                    db.record_events(conn, ticker, events)
            bundle, report, _ = read_inputs(ticker)
            with db.connect() as conn:
                balance = bundle["data_quality"]["financial_integrity"]
                claims = audit_claims(report, bundle)
                balance_record = research_store.save(conn, "BALANCE_AUDIT", ticker + ":" + digest(balance), balance, ticker)
                claim_record = research_store.save(conn, "CLAIM_AUDIT", ticker + ":" + digest({"report": report, "audit": claims}),
                    {"report_id": (report or {}).get("analysis_id"), "report_as_of": (report or {}).get("as_of"), **claims}, ticker)
            rows.append({"ticker": ticker, "balance": balance, "claim_audit": claims,
                         "balance_record": balance_record["record_id"], "claim_record": claim_record["record_id"]})
        except Exception as exc:
            rows.append({"ticker": ticker, "status": "ERROR", "error_code": type(exc).__name__})
        print(json.dumps({"ticker": ticker, "status": rows[-1].get("status", "AUDITED")} ), flush=True)
    return {"status": "FAILED" if any(r.get("status") == "ERROR" for r in rows) else "AUDITED",
            "expected": len(companies), "audited": sum(r.get("status") != "ERROR" for r in rows),
            "financially_verified": sum(r.get("balance", {}).get("status") == "VERIFIED" for r in rows), "rows": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--refresh-sec", action="store_true")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--cycle", choices=["AAPL", "MSFT", "UBER", "HIMS", "VZ", "all"])
    parser.add_argument("--experiment", action="store_true")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    result = {"started_at": now.isoformat(), "trade_execution": False}
    failed = False
    try:
        if args.reconcile or args.refresh_sec:
            result["reconciliation"] = reconcile(refresh_sec=args.refresh_sec)
            failed |= result["reconciliation"]["status"] == "FAILED"
        if args.cycle:
            from stock_machine.agents.contracts import PILOT
            from stock_machine.research_cycle import run
            result["cycles"] = [run(t, "operations:" + now.date().isoformat()) for t in (PILOT if args.cycle == "all" else [args.cycle])]
        if args.index:
            from scripts.build_coverage_snapshot import main as index
            result["index_exit_code"] = index()
            failed |= result["index_exit_code"] != 0
        if args.experiment:
            from stock_machine.prospective_experiment import run
            result["experiment"] = run()
            failed |= result["experiment"].get("status", "").startswith(("BLOCKED", "INVALID"))
    except Exception as exc:
        result["error_code"] = type(exc).__name__
        failed = True
    result["status"] = "FAILED" if failed else "COMPLETED"
    path = Path("data/research_operations") / (now.strftime("%Y%m%dT%H%M%S") + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, default=str) + "\n")
    print(json.dumps({"status": result["status"], "evidence_path": str(path)}))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
