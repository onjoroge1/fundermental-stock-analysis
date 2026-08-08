"""Phase C: the smallest honest ML layer — walk-forward ridge regression
predicting 12-month cross-sectional excess return from the point-in-time
factor panel.

Protocol (each rule exists to prevent a specific self-deception):
- Features are z-scored WITHIN each date's cross-section (levels drift over
  a decade; ranks don't).
- Embargo: a training observation is usable only when its entire 12-month
  forward window closed before the test date (as_of <= test − 370 days).
  Without this, the model trains on returns that overlap what it predicts.
- Missing features impute to the cross-sectional mean (z = 0) — never a
  filled-in guess with information content.
- Abstention: no prediction with fewer than MIN_TRAIN_DATES of history.
- Judged by the same Spearman-IC harness as everything else, on identical
  test dates, against the same baselines.

Pure Python (Gauss-Jordan solve) — 11 features do not need numpy."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .evaluate import spearman

FEATURES = [
    ("components", "growth"),
    ("components", "profitability"),
    ("components", "earnings_quality"),
    ("components", "financial_health"),
    ("components", "capital_allocation"),
    ("components", "valuation"),
    ("factors", "earnings_yield_pct"),
    ("factors", "fcf_yield_pct"),
    ("factors", "revenue_yoy_pct"),
    ("factors", "roic_pct"),
    ("factors", "momentum_12m_pct"),
]
EMBARGO_DAYS = 370
MIN_TRAIN_DATES = 8
MIN_TEST_NAMES = 8
RIDGE_ALPHA = 10.0


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gauss-Jordan with partial pivoting: solve a·x = b."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("singular matrix")
        m[col], m[pivot] = m[pivot], m[col]
        div = m[col][col]
        m[col] = [v / div for v in m[col]]
        for r in range(n):
            if r != col and m[r][col] != 0:
                factor = m[r][col]
                m[r] = [rv - factor * cv for rv, cv in zip(m[r], m[col])]
    return [m[i][n] for i in range(n)]


def ridge_fit(rows: list[tuple[list[float], float]],
              alpha: float = RIDGE_ALPHA) -> list[float]:
    k = len(rows[0][0])
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for x, y in rows:
        for i in range(k):
            xty[i] += x[i] * y
            for j in range(k):
                xtx[i][j] += x[i] * x[j]
    for i in range(k):
        xtx[i][i] += alpha
    return _solve(xtx, xty)


def _zscore_by_date(obs: list[dict]) -> dict[tuple, list[float]]:
    """(as_of, ticker) -> z-scored feature vector (missing → 0)."""
    by_date: dict[str, list[dict]] = defaultdict(list)
    for o in obs:
        by_date[o["as_of"]].append(o)
    out = {}
    for as_of, rows in by_date.items():
        stats = []
        for top, sub in FEATURES:
            vals = [r[top].get(sub) for r in rows
                    if r[top].get(sub) is not None]
            if len(vals) >= 3:
                mean = sum(vals) / len(vals)
                var = sum((v - mean) ** 2 for v in vals) / len(vals)
                stats.append((mean, var ** 0.5 or 1.0))
            else:
                stats.append((0.0, 1.0))
        for r in rows:
            vec = []
            for (top, sub), (mean, sd) in zip(FEATURES, stats):
                v = r[top].get(sub)
                vec.append(0.0 if v is None else (v - mean) / sd)
            out[(as_of, r["ticker"])] = vec
    return out


def walk_forward(obs: list[dict], horizon: str = "fwd_12m_pct") -> dict:
    usable = [o for o in obs if o["forward"].get(horizon) is not None]
    z = _zscore_by_date(usable)
    by_date: dict[str, list[dict]] = defaultdict(list)
    for o in usable:
        by_date[o["as_of"]].append(o)
    date_mean = {d: sum(r["forward"][horizon] for r in rows) / len(rows)
                 for d, rows in by_date.items()}
    dates = sorted(by_date)

    ml_ics, comp_ics, per_date = [], [], []
    weights_last = None
    for t in dates:
        test_rows = by_date[t]
        if len(test_rows) < MIN_TEST_NAMES:
            continue
        cutoff = (date.fromisoformat(t)
                  - timedelta(days=EMBARGO_DAYS)).isoformat()
        train_dates = [d for d in dates if d <= cutoff]
        if len(train_dates) < MIN_TRAIN_DATES:
            continue
        train = [(z[(d, r["ticker"])],
                  r["forward"][horizon] - date_mean[d])
                 for d in train_dates for r in by_date[d]]
        try:
            w = ridge_fit(train)
        except ValueError:
            continue
        weights_last = w
        preds = [sum(a * b for a, b in zip(z[(t, r["ticker"])], w))
                 for r in test_rows]
        actual = [r["forward"][horizon] - date_mean[t] for r in test_rows]
        ic = spearman(preds, actual)
        comp = spearman([r["composite"] for r in test_rows], actual)
        if ic is None:
            continue
        ml_ics.append(ic)
        if comp is not None:
            comp_ics.append(comp)
        rev_pairs = [(r["factors"].get("revenue_yoy_pct"),
                      r["forward"][horizon] - date_mean[t])
                     for r in test_rows
                     if r["factors"].get("revenue_yoy_pct") is not None]
        rev_ic = spearman([p[0] for p in rev_pairs],
                          [p[1] for p in rev_pairs])
        per_date.append({"as_of": t, "n": len(test_rows),
                         "ml_ic": round(ic, 3),
                         "composite_ic": round(comp, 3) if comp else None,
                         "revenue_yoy_ic": (round(rev_ic, 3)
                                            if rev_ic is not None else None)})

    if not ml_ics:
        return {"status": "INSUFFICIENT_HISTORY",
                "reason": f"need {MIN_TRAIN_DATES}+ embargoed training dates"}
    mean = sum(ml_ics) / len(ml_ics)
    rev_ics = [d["revenue_yoy_ic"] for d in per_date
               if d["revenue_yoy_ic"] is not None]
    rev_mean = sum(rev_ics) / len(rev_ics) if rev_ics else None
    verdict = {
        "kill_criterion": "the learned model must beat the best dumb "
                          "baseline on the SAME test dates, else it is not "
                          "deployed as a ranking signal",
        "best_baseline": "revenue_yoy",
        "baseline_mean_ic_same_dates": (round(rev_mean, 4)
                                        if rev_mean is not None else None),
        "model_beats_baseline": (rev_mean is not None and mean > rev_mean),
    }
    return {
        "status": "OK",
        "horizon": horizon,
        "test_dates": len(ml_ics),
        "verdict": verdict,
        "ml_mean_ic": round(mean, 4),
        "ml_ic_positive_share": round(
            sum(1 for x in ml_ics if x > 0) / len(ml_ics), 3),
        "composite_mean_ic_same_dates": (round(
            sum(comp_ics) / len(comp_ics), 4) if comp_ics else None),
        "feature_weights_final": {
            f"{top}.{sub}": round(w, 4)
            for (top, sub), w in zip(FEATURES, weights_last)},
        "per_date": per_date,
        "protocol": {
            "embargo_days": EMBARGO_DAYS, "ridge_alpha": RIDGE_ALPHA,
            "min_train_dates": MIN_TRAIN_DATES,
            "note": "walk-forward, per-date z-scores, mean-imputation; "
                    "survivorship-biased panel — indicative only",
        },
    }
