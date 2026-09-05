"""P1-C options-implied challenger built on the macro regime model."""
from __future__ import annotations

from collections import defaultdict
from .intervals import matured_before
from datetime import date, timedelta

from .evaluate import spearman
from .model import ridge_fit
from . import macro_model
from ..options.surface_features import FEATURE_NAMES as OPTION_FEATURES

EMBARGO_DAYS = 370
MIN_TRAIN_DATES = 8
MIN_TEST_NAMES = 8
RIDGE_ALPHA = 12.0
FEATURE_NAMES = macro_model.FEATURE_NAMES + [f"options.{n}" for n in OPTION_FEATURES]


def _value(row: dict, index: int):
    if index < len(macro_model.FEATURE_NAMES):
        return macro_model._value(row, index)
    name = OPTION_FEATURES[index - len(macro_model.FEATURE_NAMES)]
    return (row.get("options_implied") or {}).get("features", {}).get(name)


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
            per_date.append({"as_of": test_date, "n": len(test_rows), "options_ic": round(ic, 3)})

    if not ics:
        return {"status": "INSUFFICIENT_HISTORY"}

    macro = macro_model.walk_forward(obs, horizon=horizon)
    mean_ic = sum(ics) / len(ics)
    controls = [
        macro.get("macro_mean_ic") if macro.get("status") == "OK" else None,
        macro.get("p1a_regime_mean_ic") if macro.get("status") == "OK" else None,
        macro.get("p0_unified_mean_ic") if macro.get("status") == "OK" else None,
        macro.get("best_dumb_baseline_mean_ic") if macro.get("status") == "OK" else None,
    ]
    valid = [x for x in controls if x is not None]
    hurdle = max(valid) if valid else None
    return {
        "status": "OK",
        "horizon": horizon,
        "test_dates": len(ics),
        "options_mean_ic": round(mean_ic, 4),
        "options_ic_positive_share": round(sum(x > 0 for x in ics) / len(ics), 3),
        "macro_mean_ic": controls[0],
        "p1a_regime_mean_ic": controls[1],
        "p0_unified_mean_ic": controls[2],
        "best_dumb_baseline_mean_ic": controls[3],
        "feature_weights_final": {n: round(w, 4) for n, w in zip(FEATURE_NAMES, weights_last or [])},
        "verdict": {
            "hurdle_mean_ic": round(hurdle, 4) if hurdle is not None else None,
            "options_model_beats_all_controls": bool(hurdle is not None and mean_ic > hurdle),
            "kill_criterion": "options-implied challenger must beat macro, regime, P0, and dumb baselines on identical embargoed dates",
        },
        "per_date": per_date,
    }
