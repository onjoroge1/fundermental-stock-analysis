"""Outcome scorer: grades frozen analysis-report forecasts against what
actually happened, once each horizon matures.

Falsifiability rules:
- Forecasts are never edited; outcomes are written to a separate table keyed
  by (report_id, horizon) and scored exactly once.
- Returns are measured on the adjusted close (total-return-like); the
  bear–bull range check uses the unadjusted close, because the frozen fair
  values were unadjusted price levels when written.
- A horizon is scored only when price data reaches its target date."""
from __future__ import annotations

import bisect
from datetime import date, timedelta

from . import db

HORIZON_DAYS = {"three_month": 91, "six_month": 182, "twelve_month": 365}


def _lookup(prices: list[dict], field: str):
    dates = [p["date"] for p in prices]

    def fn(d: str) -> float | None:
        i = bisect.bisect_right(dates, d) - 1
        if i < 0:
            return None
        return prices[i].get(field) or prices[i]["close"]
    return fn


def score_report(report: dict, prices: list[dict],
                 today: str) -> list[dict]:
    """Pure scoring of one report. Returns outcome rows for horizons that are
    due; horizons whose target date is past the price history are skipped."""
    as_of_date = report["as_of"][:10]
    adj = _lookup(prices, "adj_close")
    unadj = _lookup(prices, "close")
    last_price_date = prices[-1]["date"] if prices else None
    if not last_price_date:
        return []
    base_adj = adj(as_of_date)
    if not base_adj:
        return []

    out = []
    for horizon, days in HORIZON_DAYS.items():
        f = (report.get("forecasts") or {}).get(horizon)
        if not f:
            continue
        target = (date.fromisoformat(as_of_date)
                  + timedelta(days=days)).isoformat()
        if target > min(today, last_price_date):
            continue  # not due yet
        actual_adj = adj(target)
        actual_unadj = unadj(target)
        if actual_adj is None:
            continue
        actual_return = round((actual_adj / base_adj - 1) * 100, 2)
        expected = f.get("expected_return_pct")
        low, high = f.get("fair_value_low"), f.get("fair_value_high")
        out.append({
            "horizon": horizon,
            "as_of": as_of_date,
            "target_date": target,
            "base_price": base_adj,
            "actual_price": actual_adj,
            "actual_return_pct": actual_return,
            "expected_return_pct": expected,
            "error_pct": (round(actual_return - expected, 2)
                          if expected is not None else None),
            "in_range": (low <= actual_unadj <= high
                         if None not in (low, high, actual_unadj) else None),
            "direction_hit": ((actual_return > 0) == (expected > 0)
                              if expected not in (None, 0) else None),
            "classification": (report.get("conclusion") or {}).get(
                "classification"),
        })
    return out


def run(conn) -> dict:
    """Score every due, not-yet-scored (report, horizon). Idempotent."""
    with conn.cursor() as cur:
        cur.execute("""SELECT report_id, ticker, report FROM analysis_reports
                       ORDER BY saved_at""")
        reports = cur.fetchall()
        cur.execute("SELECT report_id, horizon FROM forecast_outcomes")
        already = set(cur.fetchall())

    today = date.today().isoformat()
    scored, pending = [], 0
    price_cache: dict[str, list[dict]] = {}
    for report_id, ticker, report in reports:
        if ticker not in price_cache:
            price_cache[ticker] = db.fetch_prices(conn, ticker)
        rows = score_report(report, price_cache[ticker], today)
        due_h = {r["horizon"] for r in rows}
        pending += sum(1 for h in HORIZON_DAYS
                       if (report_id, h) not in already and h not in due_h
                       and (report.get("forecasts") or {}).get(h))
        for r in rows:
            if (report_id, r["horizon"]) in already:
                continue
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO forecast_outcomes (report_id, horizon,
                       ticker, as_of, target_date, base_price, actual_price,
                       actual_return_pct, expected_return_pct, error_pct,
                       in_range, direction_hit, classification)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    (report_id, r["horizon"], ticker, r["as_of"],
                     r["target_date"], r["base_price"], r["actual_price"],
                     r["actual_return_pct"], r["expected_return_pct"],
                     r["error_pct"], r["in_range"], r["direction_hit"],
                     r["classification"]))
            conn.commit()
            scored.append({"report_id": report_id, **{k: r[k] for k in
                          ("horizon", "actual_return_pct",
                           "expected_return_pct", "in_range",
                           "direction_hit")}})
    return {"newly_scored": scored, "pending_horizons": pending}


def summary(conn) -> dict:
    """Calibration aggregates over all scored outcomes."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT horizon, count(*),
                   avg(abs(error_pct)),
                   avg(CASE WHEN in_range THEN 1.0 ELSE 0.0 END),
                   avg(CASE WHEN direction_hit THEN 1.0 ELSE 0.0 END)
            FROM forecast_outcomes GROUP BY horizon""")
        by_h = {h: {"n": n, "mean_abs_error_pct": round(float(e), 2) if e is not None else None,
                    "range_coverage": round(float(rc), 3) if rc is not None else None,
                    "direction_hit_rate": round(float(dh), 3) if dh is not None else None}
                for h, n, e, rc, dh in cur.fetchall()}
        cur.execute("""
            SELECT classification, avg(actual_return_pct), count(*)
            FROM forecast_outcomes WHERE horizon = 'twelve_month'
            GROUP BY classification""")
        by_class = {c: {"mean_12m_return_pct": round(float(r), 2), "n": n}
                    for c, r, n in cur.fetchall()}
    return {"by_horizon": by_h, "by_classification_12m": by_class,
            "note": "range_coverage should approximate the probability mass "
                    "the scenario set intended (~1.0 minus tail risk); a "
                    "direction_hit_rate near 0.5 means forecasts carry no "
                    "directional information."}
