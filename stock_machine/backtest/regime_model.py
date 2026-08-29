"""Regime-aware challenger to the P0 unified cross-sectional model.

The P0 unified learner is frozen as the control. This challenger adds causal
market/sector/breadth regime features and is only considered useful if it beats
P0 on the same embargoed out-of-sample dates.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .evaluate import spearman
from .model import ridge_fit
from .unified_model import walk_forward as p0_walk_forward
from ..regime import REGIME_FEATURE_NAMES

EMBARGO_DAYS = 370
MIN_TRAIN_DATES = 8
MIN_TEST_NAMES = 8
RIDGE_ALPHA = 12.0

BASE_FEATURES = [
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
FEATURE_NAMES = [f"{a}.{b}" for a, b in BASE_FEATURES] + [f"regime.{x}" for x in REGIME_FEATURE_NAMES]


def _value(row: dict, index: int):
    if index < len(BASE_FEATURES):
        top, sub = BASE_FEATURES[index]
        return row.get(top, {}).get(sub)
    name = REGIME_FEATURE_NAMES[index - len(BASE_FEATURES)]
    return (row.get("regime") or {}).get("features", {}).get(name)


def _zscore_by_date(obs: list[dict]) -> dict[tuple[str, str], list[float]]:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in obs:
        by_date[row["as_of"]].append(row)

    out = {}
    width = len(FEATURE_NAMES)
    for as_of, rows in by_date.items():
        stats = []
        for j in range(width):
            vals = [_value(r, j) for r in rows if _value(r, j) is not None]
            if len(vals) >= 3:
                m = sum(vals) / len(vals)
                var = sum((v - m) ** 2 for v in vals) / len(vals)
                stats.append((m, var ** 0.5 or 1.0))
            else:
                stats.append((0.0, 1.0))
        for row in rows:
            vec = []
            for j, (m, sd) in enumerate(stats):
                v = _value(row, j)
                vec.append(0.0 if v is None else (v - m) / sd)
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

    p1_ics = []
    per_date = []
    weights_last = None
    for test_date in dates:
        test_rows = by_date[test_date]
        if len(test_rows) < MIN_TEST_NAMES:
            continue
        cutoff = (date.fromisoformat(test_date) - timedelta(days=EMBARGO_DAYS)).isoformat()
        train_dates = [d for d in dates if d <= cutoff]
        if len(train_dates) < MIN_TRAIN_DATES:
            continue
        train = [
            (z[(d, r["ticker"])], r["forward"][horizon] - date_mean[d])
            for d in train_dates for r in by_date[d]
        ]
        try:
            weights = ridge_fit(train, alpha=RIDGE_ALPHA)
        except ValueError:
            continue
        weights_last = weights
        preds = [sum(a * b for a, b in zip(z[(test_date, r["ticker"])], weights))
                 for r in test_rows]
        actual = [r["forward"][horizon] - date_mean[test_date] for r in test_rows]
        ic = spearman(preds, actual)
        if ic is None:
            continue
        p1_ics.append(ic)
        per_date.append({"as_of": test_date, "n": len(test_rows), "regime_ic": round(ic, 3)})

    if not p1_ics:
        return {"status": "INSUFFICIENT_HISTORY"}

    p0 = p0_walk_forward(obs, horizon=horizon)
    p1_mean = sum(p1_ics) / len(p1_ics)
    p0_mean = p0.get("unified_mean_ic") if p0.get("status") == "OK" else None
    best_dumb = p0.get("verdict", {}).get("best_baseline_mean_ic_same_dates") if p0.get("status") == "OK" else None
    hurdle = max(v for v in [p0_mean, best_dumb] if v is not None) if any(v is not None for v in [p0_mean, best_dumb]) else None

    return {
        "status": "OK",
        "horizon": horizon,
        "test_dates": len(p1_ics),
        "regime_mean_ic": round(p1_mean, 4),
        "regime_ic_positive_share": round(sum(x > 0 for x in p1_ics) / len(p1_ics), 3),
        "p0_unified_mean_ic_same_panel": p0_mean,
        "best_dumb_baseline_mean_ic_same_panel": best_dumb,
        "feature_weights_final": {name: round(w, 4) for name, w in zip(FEATURE_NAMES, weights_last or [])},
        "verdict": {
            "hurdle_mean_ic": round(hurdle, 4) if hurdle is not None else None,
            "regime_model_beats_p0_and_baseline": bool(hurdle is not None and p1_mean > hurdle),
            "kill_criterion": "P1 regime challenger must beat P0 unified and the strongest dumb baseline on the same embargoed panel",
        },
        "per_date": per_date,
        "protocol": {"embargo_days": EMBARGO_DAYS, "ridge_alpha": RIDGE_ALPHA},
    }
