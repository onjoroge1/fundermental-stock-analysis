"""Unified point-in-time cross-sectional alpha learner.

Combines fundamentals, valuation/quality components, market momentum, and
point-in-time expectations features. The target is 12-month cross-sectional
excess return. Training uses the same long embargo discipline as the existing
ridge model so forward-return windows cannot overlap the test date.

This model is diagnostic until it beats the strongest dumb baseline on the
same test dates.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .evaluate import spearman
from .model import ridge_fit

EMBARGO_DAYS = 370
MIN_TRAIN_DATES = 8
MIN_TEST_NAMES = 8
RIDGE_ALPHA = 12.0

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
    ("expectations", "eps_revision_pct"),
    ("expectations", "revenue_revision_pct"),
    ("expectations", "latest_eps_surprise_pct"),
    ("expectations", "trailing_4q_eps_surprise_pct"),
]


def _zscore_by_date(obs: list[dict]) -> dict[tuple[str, str], list[float]]:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in obs:
        by_date[row["as_of"]].append(row)

    out = {}
    for as_of, rows in by_date.items():
        stats = []
        for top, sub in FEATURES:
            vals = [r.get(top, {}).get(sub) for r in rows
                    if r.get(top, {}).get(sub) is not None]
            if len(vals) >= 3:
                m = sum(vals) / len(vals)
                var = sum((v - m) ** 2 for v in vals) / len(vals)
                stats.append((m, var ** 0.5 or 1.0))
            else:
                stats.append((0.0, 1.0))
        for row in rows:
            vec = []
            for (top, sub), (m, sd) in zip(FEATURES, stats):
                value = row.get(top, {}).get(sub)
                vec.append(0.0 if value is None else (value - m) / sd)
            out[(as_of, row["ticker"])] = vec
    return out


def walk_forward(obs: list[dict], horizon: str = "fwd_12m_pct") -> dict:
    usable = [o for o in obs if o.get("forward", {}).get(horizon) is not None]
    z = _zscore_by_date(usable)
    by_date: dict[str, list[dict]] = defaultdict(list)
    for o in usable:
        by_date[o["as_of"]].append(o)
    dates = sorted(by_date)
    date_mean = {d: sum(r["forward"][horizon] for r in rows) / len(rows)
                 for d, rows in by_date.items()}

    model_ics: list[float] = []
    baselines: dict[str, list[float]] = defaultdict(list)
    per_date = []
    weights_last = None

    for test_date in dates:
        test_rows = by_date[test_date]
        if len(test_rows) < MIN_TEST_NAMES:
            continue
        cutoff = (date.fromisoformat(test_date)
                  - timedelta(days=EMBARGO_DAYS)).isoformat()
        train_dates = [d for d in dates if d <= cutoff]
        if len(train_dates) < MIN_TRAIN_DATES:
            continue

        train = [
            (z[(d, row["ticker"])],
             row["forward"][horizon] - date_mean[d])
            for d in train_dates for row in by_date[d]
        ]
        try:
            weights = ridge_fit(train, alpha=RIDGE_ALPHA)
        except ValueError:
            continue
        weights_last = weights
        preds = [sum(a * b for a, b in zip(z[(test_date, r["ticker"])], weights))
                 for r in test_rows]
        actual = [r["forward"][horizon] - date_mean[test_date]
                  for r in test_rows]
        ic = spearman(preds, actual)
        if ic is None:
            continue
        model_ics.append(ic)

        baseline_specs = {
            "revenue_yoy": ("factors", "revenue_yoy_pct"),
            "momentum_12m": ("factors", "momentum_12m_pct"),
            "composite": (None, "composite"),
        }
        row_result = {"as_of": test_date, "n": len(test_rows),
                      "unified_ic": round(ic, 3)}
        for name, (top, sub) in baseline_specs.items():
            values = [r.get(sub) if top is None else r.get(top, {}).get(sub)
                      for r in test_rows]
            pairs = [(v, y) for v, y in zip(values, actual) if v is not None]
            bic = spearman([p[0] for p in pairs], [p[1] for p in pairs]) if len(pairs) >= 3 else None
            if bic is not None:
                baselines[name].append(bic)
                row_result[f"{name}_ic"] = round(bic, 3)
            else:
                row_result[f"{name}_ic"] = None
        per_date.append(row_result)

    if not model_ics:
        return {"status": "INSUFFICIENT_HISTORY",
                "reason": f"need {MIN_TRAIN_DATES}+ embargoed training dates"}

    model_mean = sum(model_ics) / len(model_ics)
    baseline_means = {
        name: (sum(vals) / len(vals) if vals else None)
        for name, vals in baselines.items()
    }
    valid_baselines = {k: v for k, v in baseline_means.items() if v is not None}
    best_name, best_value = (max(valid_baselines.items(), key=lambda kv: kv[1])
                             if valid_baselines else (None, None))

    return {
        "status": "OK",
        "horizon": horizon,
        "test_dates": len(model_ics),
        "unified_mean_ic": round(model_mean, 4),
        "unified_ic_positive_share": round(sum(x > 0 for x in model_ics) / len(model_ics), 3),
        "baseline_mean_ic_same_dates": {k: (round(v, 4) if v is not None else None)
                                        for k, v in baseline_means.items()},
        "feature_weights_final": {
            f"{top}.{sub}": round(w, 4)
            for (top, sub), w in zip(FEATURES, weights_last or [])
        },
        "verdict": {
            "best_baseline": best_name,
            "best_baseline_mean_ic_same_dates": (round(best_value, 4)
                                                  if best_value is not None else None),
            "model_beats_baseline": bool(best_value is not None and model_mean > best_value),
            "kill_criterion": "unified model must beat the strongest dumb baseline on identical embargoed test dates",
        },
        "per_date": per_date,
        "protocol": {
            "embargo_days": EMBARGO_DAYS,
            "ridge_alpha": RIDGE_ALPHA,
            "min_train_dates": MIN_TRAIN_DATES,
            "note": "cross-sectional per-date z-scores; missing values impute to cross-sectional mean; no promotion without out-of-sample baseline victory",
        },
    }
