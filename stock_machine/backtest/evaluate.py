"""Backtest evaluation: cross-sectional rank IC and quintile spreads for the
composite score and each dumb baseline, with a pre-committed kill criterion.

All returns are measured EXCESS of the date's universe equal-weight mean —
cross-sectional skill, not market drift. IC is Spearman (average-rank ties).
"""
from __future__ import annotations

import math
from collections import defaultdict
from .statistics import mean_uncertainty

FACTOR_SOURCES = {
    "composite_score": ("composite", None),
    "valuation_component": ("components", "valuation"),
    "growth_component": ("components", "growth"),
    "earnings_yield": ("factors", "earnings_yield_pct"),
    "fcf_yield": ("factors", "fcf_yield_pct"),
    "revenue_yoy": ("factors", "revenue_yoy_pct"),
    "roic": ("factors", "roic_pct"),
    "momentum_12m": ("factors", "momentum_12m_pct"),
}
BASELINES = ["earnings_yield", "fcf_yield", "revenue_yoy", "roic",
             "momentum_12m"]
MIN_NAMES_PER_DATE = 8
N_QUANTILES = 5


def _extract(obs: dict, source: tuple) -> float | None:
    top, sub = source
    v = obs.get(top)
    if sub is not None and isinstance(v, dict):
        v = v.get(sub)
    return v


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx = sum(rx) / len(rx)
    my = sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)


def _quantile_means(pairs: list[tuple[float, float]]) -> list[float] | None:
    """pairs: (factor, excess_return) → mean excess return per quantile,
    quantile 0 = highest factor value."""
    if len(pairs) < N_QUANTILES * 2:
        return None
    ordered = sorted(pairs, key=lambda p: -p[0])
    n = len(ordered)
    out = []
    for q in range(N_QUANTILES):
        lo = round(q * n / N_QUANTILES)
        hi = round((q + 1) * n / N_QUANTILES)
        bucket = ordered[lo:hi]
        out.append(sum(r for _, r in bucket) / len(bucket))
    return out


def evaluate(observations: list[dict], horizon: str = "fwd_12m_pct") -> dict:
    by_date: dict[str, list[dict]] = defaultdict(list)
    for o in observations:
        if o["forward"].get(horizon) is not None:
            by_date[o["as_of"]].append(o)

    ic_series: dict[str, list[float]] = defaultdict(list)
    paired_advantages: dict[str, list[float]] = defaultdict(list)
    quant_accum: dict[str, list[list[float]]] = defaultdict(list)
    dates_used = 0
    for as_of, rows in sorted(by_date.items()):
        if len(rows) < MIN_NAMES_PER_DATE:
            continue
        rets = [r["forward"][horizon] for r in rows]
        mean_ret = sum(rets) / len(rets)
        dates_used += 1
        # Every verdict compares exactly the same ticker/date information set.
        for baseline in BASELINES:
            paired = [r for r in rows if _extract(r, FACTOR_SOURCES[baseline]) is not None
                      and _extract(r, FACTOR_SOURCES["composite_score"]) is not None]
            if len(paired) < MIN_NAMES_PER_DATE:
                continue
            actual = [r["forward"][horizon] for r in paired]
            ci = spearman([r["composite"] for r in paired], actual)
            bi = spearman([_extract(r, FACTOR_SOURCES[baseline]) for r in paired], actual)
            if ci is not None and bi is not None:
                paired_advantages[baseline].append(ci - bi)
        for name, source in FACTOR_SOURCES.items():
            pairs = [( _extract(r, source), r["forward"][horizon] - mean_ret)
                     for r in rows if _extract(r, source) is not None]
            if len(pairs) < MIN_NAMES_PER_DATE:
                continue
            ic = spearman([p[0] for p in pairs], [p[1] for p in pairs])
            if ic is not None:
                ic_series[name].append(ic)
            qm = _quantile_means(pairs)
            if qm:
                quant_accum[name].append(qm)

    factors = {}
    for name in FACTOR_SOURCES:
        ics = ic_series.get(name, [])
        if not ics:
            continue
        mean_ic = sum(ics) / len(ics)
        uncertainty = mean_uncertainty(ics, lags={"fwd_3m_pct": 1, "fwd_6m_pct": 2, "fwd_12m_pct": 4}[horizon])
        std = (math.sqrt(sum((x - mean_ic) ** 2 for x in ics) / (len(ics) - 1))
               if len(ics) > 1 else None)
        qms = quant_accum.get(name, [])
        q_avg = ([round(sum(col) / len(col), 2) for col in zip(*qms)]
                 if qms else None)
        factors[name] = {
            "mean_ic": round(mean_ic, 4),
            "ic_positive_share": round(
                sum(1 for x in ics if x > 0) / len(ics), 3),
            "ic_tstat": round(uncertainty["tstat"], 2) if uncertainty["tstat"] is not None else None,
            "ic_uncertainty": uncertainty,
            "n_dates": len(ics),
            "quintile_mean_excess_pct": q_avg,  # index 0 = top quintile
            "top_minus_bottom_pct": (round(q_avg[0] - q_avg[-1], 2)
                                     if q_avg else None),
        }

    comp = factors.get("composite_score", {})
    best_baseline = None
    for b in BASELINES:
        if b in factors and (best_baseline is None
                             or factors[b]["mean_ic"]
                             > factors[best_baseline]["mean_ic"]):
            best_baseline = b
    verdict = None
    if comp and best_baseline:
        comparisons = {b: mean_uncertainty(paired_advantages[b],
                       lags={"fwd_3m_pct": 1, "fwd_6m_pct": 2, "fwd_12m_pct": 4}[horizon],
                       alpha=0.05 / len(BASELINES)) for b in BASELINES}
        beats = all(v["status"] == "OK" and v["lower"] > 0 for v in comparisons.values())
        verdict = {
            "kill_criterion": "paired IC advantage must have a positive dependence-aware confidence bound against every declared baseline; Bonferroni-adjusted across baselines",
            "composite_mean_ic": comp["mean_ic"],
            "best_baseline": best_baseline,
            "best_baseline_mean_ic": factors[best_baseline]["mean_ic"],
            "composite_beats_baselines": beats,
            "paired_baseline_comparisons": comparisons,
            "status": "RESEARCH_ONLY",
        }
    return {"horizon": horizon, "dates_used": dates_used,
            "factors": factors, "verdict": verdict}
