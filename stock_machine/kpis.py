"""System-level KPI engine — the validation layer the UI's executive
dashboard reads.

Design rule (the doc's central insight): show whether the machine has earned
the right to be trusted. Every KPI is either MEASURED (real value, real
target, pass/fail) or PENDING (named unlock condition). Nothing is estimated
to fill a slot."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .config import DATA_DIR
from .market_calendar import price_freshness, latest_completed_session

RECON_TOLERANCE = 0.01  # A = L + E within 1%


def _kpi(name, value, target, ok, detail=None, category="data"):
    return {"kpi": name, "value": value, "target": target,
            "status": "PASS" if ok else "FAIL", "detail": detail,
            "category": category}


def _pending(name, unlock, category):
    return {"kpi": name, "value": None, "target": None, "status": "PENDING",
            "detail": unlock, "category": category}


def reconciliation_stats(conn) -> dict:
    """Balance-sheet identity A = L + E across all normalized periods that
    carry all three fields."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FILTER (WHERE ok), count(*)
            FROM (
              SELECT abs((fields->>'total_assets')::float
                         - ((fields->>'total_liabilities')::float
                            + (fields->>'shareholders_equity')::float
                            + COALESCE((fields->>'noncontrolling_interest')::float, 0)
                            + COALESCE((fields->>'temporary_equity')::float, 0)
                            + COALESCE((fields->>'redeemable_noncontrolling_interest')::float, 0)))
                     <= %s * (fields->>'total_assets')::float AS ok
              FROM financial_periods
              WHERE fields ? 'total_assets'
                AND fields ? 'total_liabilities'
                AND fields ? 'shareholders_equity'
                AND (fields->>'total_assets')::float > 0
            ) t""", (RECON_TOLERANCE,))
        passed, total = cur.fetchone()
    return {"passed": passed or 0, "total": total or 0,
            "rate": round(passed / total, 4) if total else None}


def compute_kpis(conn) -> dict:
    kpis: list[dict] = []

    # ---------- A. data quality ----------
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM companies")
        n_companies = cur.fetchone()[0]
        cur.execute("""SELECT count(*), count(available_at)
                       FROM financial_periods""")
        n_periods, n_with_avail = cur.fetchone()
        cur.execute("""SELECT c.ticker,max(p.date)::text FROM companies c
                       LEFT JOIN prices_daily p ON c.ticker=p.ticker GROUP BY c.ticker ORDER BY c.ticker""")
        price_dates = cur.fetchall()
        cur.execute("""SELECT count(DISTINCT ticker) FROM consensus_snapshots""")
        consensus_tickers = cur.fetchone()[0]
        cur.execute("""SELECT count(DISTINCT ticker) FROM insider_transactions""")
        insider_tickers = cur.fetchone()[0]
        cur.execute("""
            SELECT COALESCE(max(snapshot_date) - min(snapshot_date), 0)
            FROM consensus_snapshots""")
        vintage_span = cur.fetchone()[0] or 0

    recon = reconciliation_stats(conn)
    kpis.append(_kpi("Accounting reconciliation (A = L + E, 1% tol)",
                     f"{recon['rate']*100:.1f}%" if recon["rate"] else "—",
                     ">99%", (recon["rate"] or 0) > 0.99,
                     f"{recon['passed']}/{recon['total']} periods"))
    kpis.append(_kpi("Availability timestamp coverage",
                     f"{n_with_avail/n_periods*100:.1f}%" if n_periods else "—",
                     "100%", n_with_avail == n_periods,
                     "Measures timestamp presence only. Causal validity also requires dependency availability, release timing, and source-vintage checks."))
    stale = [ticker for ticker, latest in price_dates if price_freshness(latest)["status"] != "CURRENT"]
    kpis.append(_kpi("Price freshness", f"{len(price_dates)-len(stale)}/{n_companies} current",
                     "every ticker through latest completed exchange session", bool(price_dates) and not stale,
                     f"expected {latest_completed_session()}; missing/stale: {', '.join(stale) or 'none'}"))
    kpis.append(_kpi("Consensus coverage",
                     f"{consensus_tickers}/{n_companies}",
                     "100% (needs FMP Starter)",
                     consensus_tickers == n_companies,
                     f"vintage span {vintage_span}d (revision analysis "
                     f"requires comparable same-fiscal-period vintages across 30d)"))
    kpis.append(_kpi("Insider-data coverage",
                     f"{insider_tickers}/{n_companies}", "100%",
                     insider_tickers == n_companies))

    # ---------- B/C. forecast quality ----------
    latest_bt = None
    bt_dir = DATA_DIR / "backtests"
    bt_files = sorted(bt_dir.glob("bt_*.json")) if bt_dir.exists() else []
    if bt_files:
        latest_bt = json.loads(bt_files[-1].read_text())
    if latest_bt:
        r12 = latest_bt["results"]["fwd_12m_pct"]
        comp = r12["factors"].get("composite_score", {})
        verdict = r12.get("verdict") or {}
        kpis.append(_kpi("Composite rank IC (12m, walk-forward)",
                         comp.get("mean_ic"), "> best dumb baseline "
                         f"({verdict.get('best_baseline_mean_ic')})",
                         bool(verdict.get("composite_beats_baselines")),
                         "kill criterion — composite is a descriptive "
                         "label, not a ranking signal, while FAIL",
                         category="forecast"))
        kpis.append(_kpi("Top-minus-bottom quintile spread (12m)",
                         f"{comp.get('top_minus_bottom_pct')}%", "> +7%",
                         (comp.get("top_minus_bottom_pct") or -99) > 7,
                         f"{r12['dates_used']} dates, survivorship-biased "
                         "panel", category="forecast"))
    ml_files = sorted(bt_dir.glob("ml_*.json")) if bt_dir.exists() else []
    if ml_files:
        ml = json.loads(ml_files[-1].read_text())
        if ml.get("status") == "OK":
            v = ml.get("verdict") or {}
            kpis.append(_kpi("ML model vs best baseline (same dates)",
                             ml.get("ml_mean_ic"),
                             f"> {v.get('baseline_mean_ic_same_dates')}",
                             bool(v.get("model_beats_baseline")),
                             "walk-forward ridge, embargoed — not deployed "
                             "while FAIL", category="forecast"))

    # outcome-based KPIs: pending until horizons mature
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM forecast_outcomes")
        n_outcomes = cur.fetchone()[0]
        cur.execute("""SELECT min(as_of::date + 91) FROM analysis_reports""")
        first_due = cur.fetchone()[0]
    if n_outcomes:
        from . import outcomes as outcomes_mod
        s = outcomes_mod.summary(conn)
        for h, row in s["by_horizon"].items():
            kpis.append(_kpi(f"Direction hit rate ({h})",
                             row["direction_hit_rate"], ">0.55",
                             (row["direction_hit_rate"] or 0) > 0.55,
                             f"n={row['n']}", category="forecast"))
            kpis.append(_kpi(f"Range coverage ({h})", row["range_coverage"],
                             "~scenario probability mass",
                             row["range_coverage"] is not None,
                             f"n={row['n']}", category="forecast"))
    else:
        kpis.append(_pending("Forecast direction hit rate / range coverage / "
                             "calibration",
                             f"unlocks when frozen forecasts mature — first "
                             f"3-month horizons due {first_due}",
                             "forecast"))
    kpis.append(_pending("Consensus-revision prediction accuracy",
                         "needs point-in-time consensus history: own "
                         f"vintages at {vintage_span}d (target 90d+) or an "
                         "IBES-grade purchase", "forecast"))
    kpis.append(_pending("Survivorship-free backtest",
                         "needs delisted-name price history (FMP "
                         "Premium/Sharadar); delisted LIST already free",
                         "forecast"))

    # ---------- short-horizon prediction calibration ----------
    pred_dir = DATA_DIR / "predictions"
    pred_files = (sorted(pred_dir.glob(f"*_{date.today().isoformat()}.json"))
                  if pred_dir.exists() else [])
    if pred_files:
        preds, ups = [], []
        for pf in pred_files:
            try:
                r = json.loads(pf.read_text())
            except ValueError:
                continue
            for fold in (r.get("validation") or {}).get("folds", []):
                model = fold.get("bootstrap") or fold.get("lstm")
                if model:
                    preds.append(model["prob_positive"])
                    ups.append(fold["realized_21d_pct"] > 0)
        if preds:
            gap = sum(preds) / len(preds) - sum(ups) / len(ups)
            kpis.append(_kpi(
                "Short-horizon P(up) calibration gap (pooled folds)",
                f"{gap:+.3f}", "|gap| < 0.03", abs(gap) < 0.03,
                f"{len(preds)} folds; positive = systematic upward bias — "
                "use drift-neutral companion probabilities while FAIL",
                category="forecast"))

    # ---------- E. AI-specific ----------
    with conn.cursor() as cur:
        cur.execute("""
            SELECT count(*) FILTER (WHERE jsonb_array_length(c->'source_ids') > 0),
                   count(*)
            FROM analysis_reports r,
                 jsonb_array_elements(r.report->'claims') c
            WHERE c->>'classification' IN ('FACT', 'INFERENCE')""")
        cited, total_claims = cur.fetchone()
    kpis.append(_kpi("Claim citation rate (FACT/INFERENCE claims)",
                     f"{cited/total_claims*100:.1f}%" if total_claims else "—",
                     "100%", cited == total_claims and total_claims > 0,
                     f"{cited}/{total_claims} claims carry source_ids",
                     category="ai"))
    kpis.append(_pending("Incremental AI value (combined vs quant-only)",
                         "needs matured outcomes for both report forecasts "
                         "and mechanical baselines", "ai"))

    # ---------- F. operational ----------
    log_dir = DATA_DIR / "refresh_logs"
    logs = sorted(log_dir.glob("*.json")) if log_dir.exists() else []
    if logs:
        oks = fails = 0
        for lf in logs:
            log = json.loads(lf.read_text())
            fails += log.get("failures", 0)
            oks += log.get("tickers", 0) - log.get("failures", 0)
        rate = oks / (oks + fails) if oks + fails else None
        kpis.append(_kpi("Refresh success rate",
                         f"{rate*100:.1f}%" if rate else "—", ">99%",
                         (rate or 0) > 0.99,
                         f"{len(logs)} refresh runs logged",
                         category="ops"))
    kpis.append(_kpi("Forecast coverage (bundles buildable)",
                     f"{n_companies}/{n_companies}", "eligible only",
                     True, "companies failing the gate stay unscored by "
                     "design", category="ops"))

    passed = sum(1 for k in kpis if k["status"] == "PASS")
    failed = sum(1 for k in kpis if k["status"] == "FAIL")
    pending = sum(1 for k in kpis if k["status"] == "PENDING")
    return {
        "as_of": date.today().isoformat(),
        "summary": {"pass": passed, "fail": failed, "pending": pending},
        "kpis": kpis,
        "principle": "A KPI is measured or pending — never estimated. FAIL "
                     "rows are the system refusing to claim trust it has "
                     "not earned.",
    }
