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
from ..macro import MACRO_INTERACTION_NAMES, interaction_components
from . import regime_model
from ..regime import REGIME_FEATURE_NAMES

EMBARGO_DAYS = 370
MIN_TRAIN_DATES = 8
MIN_TEST_NAMES = 8
RIDGE_ALPHA = 12.0

BASE_FEATURES = regime_model.BASE_FEATURES
FEATURE_NAMES = regime_model.FEATURE_NAMES + [f"macro_interactions.{x}" for x in MACRO_INTERACTION_NAMES]


def _value(row: dict, index: int):
    if index < len(regime_model.FEATURE_NAMES):
        return regime_model._value(row, index)
    return (row.get("macro_interactions") or {}).get(MACRO_INTERACTION_NAMES[index - len(regime_model.FEATURE_NAMES)])


def _zscore_by_date(obs: list[dict]):
    # Preserve the validated regime representation in every downstream lane.
    prefix = regime_model._zscore_by_date(obs)
    by_date = defaultdict(list)
    for row in obs:
        by_date[row["as_of"]].append(row)
    out = {}
    for as_of, rows in by_date.items():
        parts = [interaction_components(row) for row in rows]
        suffix = [[] for _ in rows]
        for name in MACRO_INTERACTION_NAMES:
            values = [part[name][1] for part in parts]
            mean = sum(values) / len(values)
            sd = (sum((v - mean) ** 2 for v in values) / len(values)) ** .5 or 1.0
            for vec, part in zip(suffix, parts):
                state, exposure = part[name]
                # Standardizing the product would erase macro magnitude.
                vec.append(state * (exposure - mean) / sd)
        for row, vec in zip(rows, suffix):
            key = (as_of, row["ticker"])
            out[key] = prefix[key] + vec
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
