"""Paired, time-dependent evidence for fixed research challengers."""
from .evaluate import BASELINES, FACTOR_SOURCES, MIN_NAMES_PER_DATE, _extract, spearman
from .statistics import mean_uncertainty


def baseline_scores(rows, predictions, actual):
    out = {}
    for name in BASELINES:
        triples = [(p, _extract(r, FACTOR_SOURCES[name]), y)
                   for r, p, y in zip(rows, predictions, actual)
                   if _extract(r, FACTOR_SOURCES[name]) is not None]
        if len(triples) < MIN_NAMES_PER_DATE:
            continue
        model_ic = spearman([x[0] for x in triples], [x[2] for x in triples])
        baseline_ic = spearman([x[1] for x in triples], [x[2] for x in triples])
        if model_ic is not None and baseline_ic is not None:
            out[name] = {"model_ic": model_ic, "baseline_ic": baseline_ic, "n": len(triples)}
    return out


def comparison_series(per_date, model_key):
    return [{"as_of": r["as_of"], "tickers": r["tickers"], "ic": r[model_key]} for r in per_date]


def evidence(per_date, model_key, horizon, controls=None, *, include_baselines=True):
    controls = controls or {}
    names = [*(BASELINES if include_baselines else []), *controls]
    comparisons = {}
    for name in names:
        if name in controls:
            other = {r["as_of"]: r for r in controls[name]}
            pairs = [(r[model_key], other[r["as_of"]]["ic"]) for r in per_date
                     if r["as_of"] in other and r["tickers"] == other[r["as_of"]]["tickers"]]
        else:
            pairs = [(r["paired_baselines"][name]["model_ic"], r["paired_baselines"][name]["baseline_ic"])
                     for r in per_date if name in r.get("paired_baselines", {})]
        comparisons[name] = {
            **mean_uncertainty([a-b for a, b in pairs],
                               lags={"fwd_3m_pct": 1, "fwd_6m_pct": 2, "fwd_12m_pct": 4}[horizon],
                               alpha=0.05 / (21 * max(1, len(names)))),
            "candidate_mean_ic": sum(a for a, _ in pairs)/len(pairs) if pairs else None,
            "control_mean_ic": sum(b for _, b in pairs)/len(pairs) if pairs else None,
        }
    return {"passes": bool(comparisons) and all(v["status"] == "OK" and v["lower"] > 0 for v in comparisons.values()),
            "comparisons": comparisons,
            "method": "identical names and dates; Newey-West uncertainty; Bonferroni across declared controls, seven learners and three horizons",
            "scope": "exploratory historical evidence; independent prospective confirmation required"}
