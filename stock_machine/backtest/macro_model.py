"""Macro-interaction challenger built on top of P1-A regime intelligence."""
from __future__ import annotations

from collections import defaultdict
from .intervals import matured_before
from .comparisons import baseline_scores, comparison_series, evidence
from datetime import date, timedelta

from .evaluate import spearman
from .model import ridge_fit
from .regime_model import walk_forward as p1a_walk_forward
from .unified_model import walk_forward as p0_walk_forward
from ..macro import MACRO_INTERACTION_NAMES
from ..regime import REGIME_FEATURE_NAMES

EMBARGO_DAYS = 370
MIN_TRAIN_DATES = 8
MIN_TEST_NAMES = 8
RIDGE_ALPHA = 12.0

BASE_FEATURES = [
    ("components", "growth"), ("components", "profitability"),
    ("components", "earnings_quality"), ("components", "financial_health"),
    ("components", "capital_allocation"), ("components", "valuation"),
    ("factors", "earnings_yield_pct"), ("factors", "fcf_yield_pct"),
    ("factors", "revenue_yoy_pct"), ("factors", "roic_pct"),
    ("factors", "momentum_12m_pct"),
    ("expectations", "eps_revision_pct"),
    ("expectations", "revenue_revision_pct"),
    ("expectations", "latest_eps_surprise_pct"),
    ("expectations", "trailing_4q_eps_surprise_pct"),
]

FEATURE_NAMES = (
    [f"{a}.{b}" for a, b in BASE_FEATURES]
    + [f"regime.{x}" for x in REGIME_FEATURE_NAMES]
    + [f"macro_interactions.{x}" for x in MACRO_INTERACTION_NAMES]
)


def _value(row: dict, index: int):
    if index < len(BASE_FEATURES):
        top, sub = BASE_FEATURES[index]
        return row.get(top, {}).get(sub)
    index -= len(BASE_FEATURES)
    if index < len(REGIME_FEATURE_NAMES):
        return (row.get("regime") or {}).get("features", {}).get(REGIME_FEATURE_NAMES[index])
    index -= len(REGIME_FEATURE_NAMES)
    return (row.get("macro_interactions") or {}).get(MACRO_INTERACTION_NAMES[index])


def _zscore_by_date(obs: list[dict]):
    by_date = defaultdict(list)
    for row in obs:
        by_date[row["as_of"]].append(row)
    out = {}
    for as_of, rows in by_date.items():
        stats = []
        for j in range(len(FEATURE_NAMES)):
            vals = [_value(r, j) for r in rows if _value(r, j) is not None]
            if len(vals) >= 3:
                m = sum(vals) / len(vals)
                sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
            else:
                m, sd = 0.0, 1.0
            stats.append((m, sd))
        for row in rows:
            out[(as_of, row["ticker"])] = [
                0.0 if _value(row, j) is None else (_value(row, j) - m) / sd
                for j, (m, sd) in enumerate(stats)
            ]
    return out


def walk_forward(obs: list[dict], horizon: str = "fwd_12m_pct") -> dict:
    usable = [o for o in obs if o.get("forward", {}).get(horizon) is not None]
    z = _zscore_by_date(obs)
    by_date = defaultdict(list)
    for row in usable:
        by_date[row["as_of"]].append(row)
    dates = sorted(by_date)
    means = {d: sum(r["forward"][horizon] for r in rows) / len(rows)
             for d, rows in by_date.items()}

    ics, weights_last = [], None
    per_date = []
    for test_date in dates:
        test_rows = by_date[test_date]
        if len(test_rows) < MIN_TEST_NAMES:
            continue
        cutoff = (date.fromisoformat(test_date) - timedelta(days=EMBARGO_DAYS)).isoformat()
        train_dates = [d for d in dates if d <= cutoff
                       and all(matured_before(r, horizon, test_date) for r in by_date[d])]
        if len(train_dates) < MIN_TRAIN_DATES:
            continue
        train = [
            (z[(d, r["ticker"])], r["forward"][horizon] - means[d])
            for d in train_dates for r in by_date[d]
        ]
        try:
            weights = ridge_fit(train, alpha=RIDGE_ALPHA)
        except ValueError:
            continue
        weights_last = weights
        pred = [sum(a * b for a, b in zip(z[(test_date, r["ticker"])], weights))
                for r in test_rows]
        actual = [r["forward"][horizon] - means[test_date] for r in test_rows]
        ic = spearman(pred, actual)
        if ic is not None:
            ics.append(ic)
            per_date.append({"as_of": test_date, "n": len(test_rows), "macro_ic": ic,
                             "tickers": sorted(r["ticker"] for r in test_rows),
                             "paired_baselines": baseline_scores(test_rows, pred, actual)})

    if not ics:
        return {"status": "INSUFFICIENT_HISTORY"}

    p0 = p0_walk_forward(obs, horizon=horizon)
    p1a = p1a_walk_forward(obs, horizon=horizon)
    control_series = {"p0": comparison_series(p0.get("per_date", []), "unified_ic"),
                      "regime": comparison_series(p1a.get("per_date", []), "regime_ic")}
    paired = evidence(per_date, "macro_ic", horizon, control_series)
    macro_mean = sum(ics) / len(ics)
    hurdles = [
        p0.get("unified_mean_ic") if p0.get("status") == "OK" else None,
        p1a.get("regime_mean_ic") if p1a.get("status") == "OK" else None,
        p0.get("verdict", {}).get("best_baseline_mean_ic_same_dates")
        if p0.get("status") == "OK" else None,
    ]
    valid = [v for v in hurdles if v is not None]
    hurdle = max(valid) if valid else None

    return {
        "status": "OK",
        "horizon": horizon,
        "test_dates": len(ics),
        "per_date": per_date,
        "control_series": control_series,
        "macro_mean_ic": round(macro_mean, 4),
        "macro_ic_positive_share": round(sum(x > 0 for x in ics) / len(ics), 3),
        "p0_unified_mean_ic": hurdles[0],
        "p1a_regime_mean_ic": hurdles[1],
        "best_dumb_baseline_mean_ic": hurdles[2],
        "feature_weights_final": {name: round(w, 4) for name, w in zip(FEATURE_NAMES, weights_last or [])},
        "verdict": {
            "hurdle_mean_ic": round(hurdle, 4) if hurdle is not None else None,
            "macro_model_beats_all_controls": paired["passes"],
            "paired_evidence": paired,
            "kill_criterion": "P1-B macro-interaction challenger must beat P1-A, P0, and the strongest dumb baseline on the same embargoed panel",
        },
    }
