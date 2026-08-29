"""Outcome scoring and reliability diagnostics for P(outperform).

A stored forecast is immutable.  Once a direct-horizon target matures, this
module writes a separate realized outcome exactly once and reports calibration
without rewriting the original probability.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from math import log

from . import db
from .regime import RegimeFeatureProvider

HORIZONS = (5, 10, 20, 63, 126, 252)


def _price_map(rows: list[dict]) -> dict[str, float]:
    out = {}
    for r in rows:
        v = r.get("adj_close") or r.get("close")
        if v is not None and float(v) > 0:
            out[str(r["date"])[:10]] = float(v)
    return out


def _aligned(stock_rows: list[dict], bench_rows: list[dict]) -> list[tuple[str, float, float]]:
    s, b = _price_map(stock_rows), _price_map(bench_rows)
    dates = sorted(set(s) & set(b))
    return [(d, s[d], b[d]) for d in dates]


def _realized(aligned: list[tuple[str, float, float]], as_of: str, horizon: int):
    dates = [r[0] for r in aligned]
    i = bisect.bisect_left(dates, as_of)
    if i >= len(dates) or dates[i] != as_of or i + horizon >= len(aligned):
        return None
    d0, s0, b0 = aligned[i]
    d1, s1, b1 = aligned[i + horizon]
    excess_log = log(s1 / s0) - log(b1 / b0)
    return d1, (pow(2.718281828459045, excess_log) - 1.0) * 100.0


def score_pending(conn, benchmark: str = "SPY") -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT ticker, as_of::text, model_version, payload
                 FROM prediction_forecasts
                WHERE status = 'OK' ORDER BY as_of, ticker"""
        )
        forecasts = cur.fetchall()
        cur.execute(
            """SELECT ticker, as_of::text, model_version, horizon_days
                 FROM alpha_probability_outcomes"""
        )
        existing = {(r[0], r[1], r[2], int(r[3])) for r in cur.fetchall()}

    bench_rows = db.fetch_prices(conn, benchmark)
    qqq_rows = db.fetch_prices(conn, "QQQ")
    price_cache = {benchmark: bench_rows}
    scored = []
    pending = 0

    for ticker, as_of, version, payload in forecasts:
        alpha = (payload or {}).get("alpha_forecast") or {}
        if alpha.get("status") != "OK":
            continue
        if ticker not in price_cache:
            price_cache[ticker] = db.fetch_prices(conn, ticker)
        aligned = _aligned(price_cache[ticker], bench_rows)
        regime = RegimeFeatureProvider(spy_rows=bench_rows, qqq_rows=qqq_rows).features_as_of(as_of)
        regime_label = regime.get("classification")

        for horizon in HORIZONS:
            key = (ticker, as_of, version, horizon)
            if key in existing:
                continue
            row = (alpha.get("horizons") or {}).get(str(horizon)) or {}
            p = row.get("prob_outperform")
            if row.get("status") != "OK" or p is None:
                continue
            actual = _realized(aligned, as_of, horizon)
            if actual is None:
                pending += 1
                continue
            target_date, excess = actual
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO alpha_probability_outcomes
                       (ticker, as_of, model_version, horizon_days, target_date,
                        prob_outperform, actual_excess_return_pct,
                        actual_outperform, regime)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    (ticker, as_of, version, horizon, target_date, float(p),
                     round(excess, 4), excess > 0, regime_label),
                )
            conn.commit()
            scored.append({"ticker": ticker, "as_of": as_of,
                           "horizon_days": horizon, "prob_outperform": float(p),
                           "actual_outperform": excess > 0,
                           "actual_excess_return_pct": round(excess, 3),
                           "regime": regime_label})
    return {"newly_scored": scored, "pending": pending}


def reliability(rows: list[dict]) -> dict:
    if not rows:
        return {"status": "INSUFFICIENT_DATA", "n": 0, "bins": []}
    bins = defaultdict(list)
    for r in rows:
        p = min(0.999999, max(0.0, float(r["prob_outperform"])))
        bucket = int(p * 10) / 10.0
        bins[bucket].append(r)
    rendered = []
    for lo in sorted(bins):
        values = bins[lo]
        mean_p = sum(float(r["prob_outperform"]) for r in values) / len(values)
        observed = sum(bool(r["actual_outperform"]) for r in values) / len(values)
        rendered.append({"p_low": round(lo, 1), "p_high": round(lo + 0.1, 1),
                         "n": len(values), "mean_predicted": round(mean_p, 3),
                         "observed_outperform_rate": round(observed, 3),
                         "calibration_gap": round(observed - mean_p, 3)})
    brier = sum((float(r["prob_outperform"]) - float(bool(r["actual_outperform"]))) ** 2
                for r in rows) / len(rows)
    ece = sum(abs(b["calibration_gap"]) * b["n"] for b in rendered) / len(rows)
    return {"status": "OK", "n": len(rows), "brier_score": round(brier, 4),
            "expected_calibration_error": round(ece, 4), "bins": rendered}


def summary(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT horizon_days, prob_outperform, actual_outperform, regime
                 FROM alpha_probability_outcomes ORDER BY as_of"""
        )
        raw = cur.fetchall()
    rows = [{"horizon_days": int(h), "prob_outperform": float(p),
             "actual_outperform": bool(a), "regime": reg}
            for h, p, a, reg in raw]
    by_horizon = {}
    by_regime = {}
    for horizon in HORIZONS:
        subset = [r for r in rows if r["horizon_days"] == horizon]
        by_horizon[str(horizon)] = reliability(subset)
    for regime in sorted({r["regime"] for r in rows if r["regime"]}):
        subset = [r for r in rows if r["regime"] == regime]
        by_regime[regime] = reliability(subset)
    return {
        "status": "OK" if rows else "PENDING",
        "n": len(rows),
        "by_horizon": by_horizon,
        "by_regime": by_regime,
        "note": "Calibration is descriptive until each bucket/regime has adequate realized outcomes; forecasts are never retroactively edited.",
    }
