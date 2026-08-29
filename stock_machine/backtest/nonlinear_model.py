"""Nonlinear tabular challenger for P1.

The model uses the exact same embargoed panel and feature contract as the
options-implied ridge challenger.  LightGBM is an optional research dependency;
production imports do not require it.  Promotion requires an out-of-sample win
against the strongest linear P1 control.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .evaluate import spearman
from . import options_model

EMBARGO_DAYS = 370
MIN_TRAIN_DATES = 8
MIN_TEST_NAMES = 8
MODEL_NAME = "lightgbm_cross_sectional"


def _new_model():
    try:
        from lightgbm import LGBMRegressor
    except ImportError:
        return None
    return LGBMRegressor(
        objective="regression_l1",
        n_estimators=120,
        learning_rate=0.035,
        num_leaves=15,
        max_depth=5,
        min_child_samples=12,
        subsample=0.85,
        colsample_bytree=0.80,
        reg_alpha=0.2,
        reg_lambda=1.0,
        random_state=41,
        n_jobs=1,
        verbosity=-1,
    )


def walk_forward(obs: list[dict], horizon: str = "fwd_12m_pct") -> dict:
    if _new_model() is None:
        return {"status": "DEPENDENCY_MISSING", "model": MODEL_NAME,
                "reason": "install optional dependency group p1"}

    usable = [o for o in obs if o.get("forward", {}).get(horizon) is not None]
    z = options_model._zscore_by_date(usable)
    by_date = defaultdict(list)
    for row in usable:
        by_date[row["as_of"]].append(row)
    dates = sorted(by_date)
    means = {d: sum(r["forward"][horizon] for r in rows) / len(rows)
             for d, rows in by_date.items()}

    ics = []
    per_date = []
    importances = None
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
        model = _new_model()
        model.fit(x_train, y_train)
        x_test = [z[(test_date, r["ticker"])] for r in test_rows]
        pred = list(model.predict(x_test))
        actual = [r["forward"][horizon] - means[test_date] for r in test_rows]
        ic = spearman(pred, actual)
        if ic is None:
            continue
        ics.append(ic)
        per_date.append({"as_of": test_date, "n": len(test_rows), "lightgbm_ic": round(ic, 3)})
        importances = list(model.feature_importances_)

    if not ics:
        return {"status": "INSUFFICIENT_HISTORY", "model": MODEL_NAME}

    linear = options_model.walk_forward(obs, horizon=horizon)
    nonlinear_mean = sum(ics) / len(ics)
    controls = [
        linear.get("options_mean_ic") if linear.get("status") == "OK" else None,
        linear.get("macro_mean_ic") if linear.get("status") == "OK" else None,
        linear.get("p1a_regime_mean_ic") if linear.get("status") == "OK" else None,
        linear.get("p0_unified_mean_ic") if linear.get("status") == "OK" else None,
        linear.get("best_dumb_baseline_mean_ic") if linear.get("status") == "OK" else None,
    ]
    valid = [x for x in controls if x is not None]
    hurdle = max(valid) if valid else None
    importance_map = {}
    if importances:
        total = sum(importances) or 1.0
        importance_map = {
            name: round(float(value) / total, 5)
            for name, value in zip(options_model.FEATURE_NAMES, importances)
        }

    return {
        "status": "OK",
        "model": MODEL_NAME,
        "horizon": horizon,
        "test_dates": len(ics),
        "lightgbm_mean_ic": round(nonlinear_mean, 4),
        "lightgbm_ic_positive_share": round(sum(x > 0 for x in ics) / len(ics), 3),
        "linear_options_mean_ic": controls[0],
        "feature_importance_final": importance_map,
        "verdict": {
            "hurdle_mean_ic": round(hurdle, 4) if hurdle is not None else None,
            "lightgbm_beats_all_controls": bool(hurdle is not None and nonlinear_mean > hurdle),
            "kill_criterion": "LightGBM must beat the strongest linear P1/P0/baseline control on identical embargoed dates",
        },
        "per_date": per_date,
    }
