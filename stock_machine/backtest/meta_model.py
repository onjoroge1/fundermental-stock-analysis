"""Leakage-safe rolling ensemble for the P1 cross-sectional models.

Each test date blends ridge and LightGBM predictions using only model ICs from
*earlier* test dates.  Current-date outcomes never influence the weights used
on that date.  Until enough history exists, the blend is equal-weighted.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .evaluate import spearman
from .model import ridge_fit
from . import options_model, nonlinear_model

EMBARGO_DAYS = 370
MIN_TRAIN_DATES = 8
MIN_TEST_NAMES = 8
RIDGE_ALPHA = 12.0
WEIGHT_LOOKBACK = 8
MIN_WEIGHT_HISTORY = 4


def rolling_weights(history: dict[str, list[float]]) -> dict[str, float]:
    names = sorted(history)
    if not names:
        return {}
    if any(len(history[n]) < MIN_WEIGHT_HISTORY for n in names):
        return {n: 1.0 / len(names) for n in names}
    scores = {
        n: max(0.0, sum(history[n][-WEIGHT_LOOKBACK:]) /
               len(history[n][-WEIGHT_LOOKBACK:]))
        for n in names
    }
    total = sum(scores.values())
    if total <= 1e-12:
        return {n: 1.0 / len(names) for n in names}
    return {n: scores[n] / total for n in names}


def walk_forward(obs: list[dict], horizon: str = "fwd_12m_pct") -> dict:
    if nonlinear_model._new_model() is None:
        return {"status": "DEPENDENCY_MISSING", "model": "rolling_p1_ensemble"}

    usable = [o for o in obs if o.get("forward", {}).get(horizon) is not None]
    z = options_model._zscore_by_date(usable)
    by_date = defaultdict(list)
    for row in usable:
        by_date[row["as_of"]].append(row)
    dates = sorted(by_date)
    means = {d: sum(r["forward"][horizon] for r in rows) / len(rows)
             for d, rows in by_date.items()}

    history = {"ridge": [], "lightgbm": []}
    ensemble_ics = []
    per_date = []
    for test_date in dates:
        test_rows = by_date[test_date]
        if len(test_rows) < MIN_TEST_NAMES:
            continue
        cutoff = (date.fromisoformat(test_date) - timedelta(days=EMBARGO_DAYS)).isoformat()
        train_dates = [d for d in dates if d <= cutoff]
        if len(train_dates) < MIN_TRAIN_DATES:
            continue
        x_train = [z[(d, r["ticker"])] for d in train_dates for r in by_date[d]]
        y_train = [r["forward"][horizon] - means[d] for d in train_dates for r in by_date[d]]
        if len(x_train) < 80:
            continue
        x_test = [z[(test_date, r["ticker"])] for r in test_rows]
        actual = [r["forward"][horizon] - means[test_date] for r in test_rows]

        ridge_w = ridge_fit(list(zip(x_train, y_train)), alpha=RIDGE_ALPHA)
        ridge_pred = [sum(a * b for a, b in zip(x, ridge_w)) for x in x_test]
        tree = nonlinear_model._new_model()
        tree.fit(x_train, y_train)
        tree_pred = list(tree.predict(x_test))

        weights = rolling_weights(history)
        ensemble_pred = [
            weights["ridge"] * rp + weights["lightgbm"] * tp
            for rp, tp in zip(ridge_pred, tree_pred)
        ]
        ens_ic = spearman(ensemble_pred, actual)
        ridge_ic = spearman(ridge_pred, actual)
        tree_ic = spearman(tree_pred, actual)
        if ens_ic is None or ridge_ic is None or tree_ic is None:
            continue

        # Update only AFTER scoring the current date, preserving causality.
        ensemble_ics.append(ens_ic)
        per_date.append({
            "as_of": test_date,
            "n": len(test_rows),
            "ridge_ic": round(ridge_ic, 3),
            "lightgbm_ic": round(tree_ic, 3),
            "ensemble_ic": round(ens_ic, 3),
            "weights": {k: round(v, 4) for k, v in weights.items()},
        })
        history["ridge"].append(ridge_ic)
        history["lightgbm"].append(tree_ic)

    if not ensemble_ics:
        return {"status": "INSUFFICIENT_HISTORY", "model": "rolling_p1_ensemble"}

    ridge_mean = sum(history["ridge"]) / len(history["ridge"])
    tree_mean = sum(history["lightgbm"]) / len(history["lightgbm"])
    ensemble_mean = sum(ensemble_ics) / len(ensemble_ics)
    hurdle = max(ridge_mean, tree_mean)
    return {
        "status": "OK",
        "model": "rolling_p1_ensemble",
        "horizon": horizon,
        "test_dates": len(ensemble_ics),
        "ensemble_mean_ic": round(ensemble_mean, 4),
        "ridge_mean_ic_same_dates": round(ridge_mean, 4),
        "lightgbm_mean_ic_same_dates": round(tree_mean, 4),
        "ensemble_ic_positive_share": round(sum(x > 0 for x in ensemble_ics) / len(ensemble_ics), 3),
        "final_weights": {k: round(v, 4) for k, v in rolling_weights(history).items()},
        "verdict": {
            "best_single_model_mean_ic": round(hurdle, 4),
            "ensemble_beats_best_single_model": ensemble_mean > hurdle,
            "kill_criterion": "rolling ensemble must beat the best constituent model on identical embargoed dates; weights may use only earlier OOS ICs",
        },
        "per_date": per_date,
    }
