"""Daily refresh: re-ingest every covered ticker, rebuild bundles, and flag —
never silently rewrite — analysis reports that new filings have made stale.

Design rule: data refreshes mechanically; analyst narratives are frozen at
their as_of date. A report whose underlying data changed (new quarter filed
after the report was written) gets a STALE marker so the analyst pass can be
re-run deliberately. Regenerating narrative text with fresh numbers would
produce claims nobody actually reviewed.
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.bundle import build_bundle, write_bundle
from stock_machine.pipeline import run as run_pipeline

LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "refresh_logs"


def _field_coverage(bundle: dict) -> dict[str, list[str]]:
    """Which canonical fields are populated in the last 4 quarters — the
    tripwire for quiet data rot (a provider/tag change shows up here as a
    field going missing, even when no 'critical' field is hit)."""
    last4 = bundle["financial_history"]["quarterly_periods"][-4:]
    present: set[str] = set()
    for p in last4:
        for stmt in ("income_statement", "balance_sheet", "cash_flow", "shares"):
            present.update(k for k, v in p.get(stmt, {}).items()
                           if v is not None)
    return {"fields_present": sorted(present)}


def _previous_log() -> dict | None:
    logs = sorted(LOG_DIR.glob("*.json"))
    return json.loads(logs[-1].read_text()) if logs else None


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    results, failures = [], 0
    prev_log = _previous_log()
    prev_coverage = {}
    if prev_log:
        prev_coverage = {r["ticker"]: set(r.get("fields_present", []))
                         for r in prev_log.get("results", [])}

    conn = db.connect()
    try:
        tickers = [c["ticker"] for c in db.list_companies(conn)]
    finally:
        conn.close()

    for t in tickers:
        entry = {"ticker": t}
        try:
            summary = run_pipeline(t)
            bundle = build_bundle(t)
            write_bundle(bundle)
            from stock_machine import monitoring, paper
            from stock_machine.peers import snapshot_metrics
            conn = db.connect()
            try:
                snapshot_metrics(conn, t, bundle)
                # invalidation monitoring: flag, never auto-act
                report = db.latest_report(conn, t)
                if report:
                    report_id = f"{t}__{report.get('as_of', '')[:10]}"
                    breaches = monitoring.check_bundle(bundle)
                    new_breaches = monitoring.record_breaches(
                        conn, t, report_id, breaches)
                    if new_breaches:
                        entry["invalidation_breaches"] = [
                            {"rule": b["rule_id"], "observed": b["observed"],
                             "threshold": b["threshold"]}
                            for b in new_breaches]
                        paper.flag_position(
                            conn, t, "invalidation breach: " + "; ".join(
                                b["rule_id"] for b in new_breaches))
            finally:
                conn.close()
            latest_q = (bundle["financial_history"]["quarterly_periods"] or [{}])[-1]
            entry.update({
                "status": "ok",
                "data_quality": bundle["data_quality"]["status"],
                "quarters": summary["quarters"],
                "latest_period": latest_q.get("period_id"),
                "latest_available_at": latest_q.get("available_at"),
                "price_date": bundle["market_snapshot"]["price_date"],
                "consensus_available": bundle["consensus"]["available"],
                **_field_coverage(bundle),
            })
            # data-rot tripwire: fields that were populated last run but
            # vanished this run mean a provider or tag change, not reality
            lost = prev_coverage.get(t, set()) - set(entry["fields_present"])
            if lost:
                entry["coverage_lost"] = sorted(lost)
                entry["coverage_alert"] = (
                    f"fields present last refresh but missing now: "
                    f"{', '.join(sorted(lost))} — investigate tag/provider "
                    "change before trusting derived metrics")
            # staleness check: has a filing arrived after the newest report?
            conn = db.connect()
            try:
                report = db.latest_report(conn, t)
            finally:
                conn.close()
            if report and latest_q.get("available_at"):
                report_date = report["as_of"][:10]
                if latest_q["available_at"] > report_date:
                    entry["report_stale"] = True
                    entry["stale_reason"] = (
                        f"{latest_q.get('period_id')} became available "
                        f"{latest_q['available_at']}, after the report of "
                        f"{report_date} — re-run the analyst pass")
        except Exception as e:
            failures += 1
            entry.update({"status": "error", "error": f"{type(e).__name__}: {e}",
                          "trace": traceback.format_exc()[-800:]})
        results.append(entry)
        print(json.dumps({k: v for k, v in entry.items()
                          if k not in ("trace", "fields_present")}))

    # paper portfolio: reconcile with latest classifications, then mark
    paper_result = {}
    try:
        from stock_machine import paper
        conn = db.connect()
        try:
            sync = paper.sync_with_reports(conn)
            nav = paper.mark(conn)
            paper_result = {"sync": sync,
                            "nav": {k: nav[k] for k in
                                    ("date", "long_ret_pct", "short_ret_pct",
                                     "ls_ret_pct", "n_long", "n_short")}}
        finally:
            conn.close()
    except Exception as e:
        paper_result = {"error": f"{type(e).__name__}: {e}"}

    # Precompute and persist probabilistic forecasts. Web requests never train.
    prediction_result = {"ok": 0, "failed": 0}
    try:
        from stock_machine.prediction import forecast
        conn = db.connect()
        try:
            for t in tickers:
                try:
                    rows = db.fetch_prices(conn, t)
                    closes = [{"date": r["date"],
                               "adj_close": r.get("adj_close") or r["close"]}
                              for r in rows]
                    r = forecast(t, closes)
                    if r["status"] == "OK":
                        db.save_prediction_forecast(conn, r)
                    prediction_result["ok" if r["status"] == "OK"
                                      else "failed"] += 1
                except Exception as exc:
                    prediction_result["failed"] += 1
                    prediction_result.setdefault("errors", []).append({
                        "ticker": t,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
        finally:
            conn.close()
    except Exception as e:
        prediction_result = {"error": f"{type(e).__name__}: {e}"}

    # grade any forecast horizons that have matured (idempotent)
    outcome_result = {"newly_scored": [], "pending_horizons": 0}
    try:
        from stock_machine import outcomes
        conn = db.connect()
        try:
            outcome_result = outcomes.run(conn)
        finally:
            conn.close()
    except Exception as e:
        outcome_result = {"error": f"{type(e).__name__}: {e}"}

    log = {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tickers": len(tickers), "failures": failures,
        "stale_reports": [r["ticker"] for r in results if r.get("report_stale")],
        "coverage_alerts": [r["ticker"] for r in results if r.get("coverage_lost")],
        "forecast_outcomes": outcome_result,
        "paper_portfolio": paper_result,
        "prediction_precompute": prediction_result,
        "new_invalidation_breaches": [
            {"ticker": r["ticker"], **b} for r in results
            for b in r.get("invalidation_breaches", [])],
        "results": results,
    }
    log_path = LOG_DIR / f"{started.strftime('%Y-%m-%d')}.json"
    log_path.write_text(json.dumps(log, indent=1))
    newly = outcome_result.get("newly_scored")
    print(f"\nrefresh complete: {len(tickers) - failures}/{len(tickers)} ok, "
          f"stale reports: {log['stale_reports'] or 'none'}, "
          f"outcomes newly scored: {len(newly) if newly is not None else 'err'} "
          f"(pending {outcome_result.get('pending_horizons', '?')})"
          f"  → {log_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
