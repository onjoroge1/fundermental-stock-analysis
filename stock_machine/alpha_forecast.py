"""Direct-horizon benchmark-relative alpha forecasting.

This module is intentionally separate from ``prediction.py``.  The existing
Prediction Lab is a calibrated risk/range forecaster and remains the primary
production model until a candidate alpha model proves edge out of sample.

P0 changes the question from "where will the stock price be?" to "will this
stock outperform its benchmark over a fixed horizon?" and trains a distinct
model for every horizon.  No recursive one-day rollout is used.

Design constraints:
- targets are cumulative STOCK MINUS BENCHMARK log returns;
- every feature is causal at the observation date;
- feature scaling is fit on the training slice only;
- each horizon is trained directly, never by recursive reuse of a 1d model;
- walk-forward evaluation purges the full target horizon;
- consensus revisions and earnings surprises are point-in-time features;
- missing expectations history is represented explicitly, never fabricated;
- deployment remains diagnostic until the model beats a zero-alpha baseline.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from math import erf, exp, log, sqrt
from statistics import mean

from .backtest.model import ridge_fit

DIRECT_HORIZONS = (5, 10, 20, 63, 126, 252)
MODEL_NAME = "direct_excess_ridge"
MODEL_VERSION = "direct-alpha.v1"
MIN_TRAIN_ROWS = 120
MIN_EVAL_ROWS = 16
RIDGE_ALPHA = 12.0
SAMPLE_STEP = 5


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return sqrt(sum((x - m) ** 2 for x in values) / (len(values) - 1))


def _safe_log_return(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    return log(b / a)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _series_by_date(rows: list[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows:
        value = row.get("adj_close") or row.get("close")
        if value is not None and float(value) > 0:
            out[str(row["date"])[:10]] = float(value)
    return out


def align_prices(stock_rows: list[dict], benchmark_rows: list[dict]) -> list[dict]:
    """Inner-join stock and benchmark prices by date; never forward-fill."""
    stock = _series_by_date(stock_rows)
    benchmark = _series_by_date(benchmark_rows)
    dates = sorted(set(stock) & set(benchmark))
    return [{"date": d, "stock": stock[d], "benchmark": benchmark[d]}
            for d in dates]


def excess_log_returns(aligned: list[dict]) -> list[float]:
    return [
        _safe_log_return(a["stock"], b["stock"])
        - _safe_log_return(a["benchmark"], b["benchmark"])
        for a, b in zip(aligned, aligned[1:])
    ]


def _rolling_sum(values: list[float], end: int, width: int) -> float:
    start = max(0, end - width)
    return sum(values[start:end])


def _rolling_vol(values: list[float], end: int, width: int) -> float:
    start = max(0, end - width)
    return _stdev(values[start:end])


def _revision_index(consensus_history: list[dict]) -> tuple[list[str], list[dict]]:
    """Collapse each snapshot day to the nearest forward-period consensus.

    We intentionally prefer quarterly rows when available and otherwise use
    the nearest annual row.  The time series is a vintage series: a feature on
    date T may only inspect snapshots with snapshot_date <= T.
    """
    by_day: dict[str, list[dict]] = {}
    for row in consensus_history:
        snap = str(row.get("snapshot_date") or "")[:10]
        if not snap:
            continue
        by_day.setdefault(snap, []).append(row)

    days: list[str] = []
    snapshots: list[dict] = []
    for snap in sorted(by_day):
        rows = by_day[snap]
        quarterly = [r for r in rows if r.get("period_type") == "quarter"]
        chosen_pool = quarterly or rows
        chosen = sorted(
            chosen_pool,
            key=lambda r: str(r.get("forecast_period_end") or "9999-12-31"),
        )[0]
        snapshots.append({
            "snapshot_date": snap,
            "eps_mean": chosen.get("eps_mean"),
            "revenue_mean": chosen.get("revenue_mean"),
        })
        days.append(snap)
    return days, snapshots


def _pct_change(new, old) -> float:
    if new is None or old is None:
        return 0.0
    old = float(old)
    new = float(new)
    if abs(old) < 1e-12:
        return 0.0
    return (new - old) / abs(old)


def expectation_features(as_of: str, consensus_history: list[dict],
                         surprises: list[dict]) -> list[float]:
    """Point-in-time revision/surprise features for one observation date."""
    days, snapshots = _revision_index(consensus_history)
    pos = bisect_right(days, as_of) - 1
    if pos >= 0:
        cur = snapshots[pos]
        prev = snapshots[pos - 1] if pos > 0 else None
        eps_rev = _pct_change(cur.get("eps_mean"),
                              prev.get("eps_mean") if prev else None)
        rev_rev = _pct_change(cur.get("revenue_mean"),
                              prev.get("revenue_mean") if prev else None)
        has_consensus = 1.0
    else:
        eps_rev = rev_rev = 0.0
        has_consensus = 0.0

    past_surprises = [r for r in surprises
                      if str(r.get("date") or "")[:10] <= as_of
                      and r.get("surprise_pct") is not None]
    if past_surprises:
        latest = float(past_surprises[-1]["surprise_pct"]) / 100.0
        trailing = mean(float(r["surprise_pct"]) for r in past_surprises[-4:]) / 100.0
        has_surprise = 1.0
    else:
        latest = trailing = 0.0
        has_surprise = 0.0
    return [eps_rev, rev_rev, latest, trailing, has_consensus, has_surprise]


def _feature_vector(aligned: list[dict], stock_rets: list[float],
                    bench_rets: list[float], excess: list[float], idx: int,
                    consensus_history: list[dict], surprises: list[dict]) -> list[float]:
    """Causal feature vector known at aligned[idx].date.

    ``idx`` is a price index; return windows therefore end at idx and never
    include the return from idx to idx+1.
    """
    date = aligned[idx]["date"]
    stock_5 = _rolling_sum(stock_rets, idx, 5)
    stock_20 = _rolling_sum(stock_rets, idx, 20)
    stock_63 = _rolling_sum(stock_rets, idx, 63)
    excess_5 = _rolling_sum(excess, idx, 5)
    excess_20 = _rolling_sum(excess, idx, 20)
    excess_63 = _rolling_sum(excess, idx, 63)
    vol_21 = _rolling_vol(stock_rets, idx, 21)
    vol_63 = _rolling_vol(stock_rets, idx, 63)
    bench_20 = _rolling_sum(bench_rets, idx, 20)
    bench_63 = _rolling_sum(bench_rets, idx, 63)

    # Relative drawdown from the stock's own trailing 63-session peak.
    start = max(0, idx - 62)
    peak = max(row["stock"] for row in aligned[start:idx + 1])
    drawdown_63 = aligned[idx]["stock"] / peak - 1.0 if peak else 0.0

    return [
        stock_5, stock_20, stock_63,
        excess_5, excess_20, excess_63,
        vol_21, vol_63, bench_20, bench_63, drawdown_63,
        *expectation_features(date, consensus_history, surprises),
    ]


def _standardize(train_x: list[list[float]], x: list[float] | None = None):
    k = len(train_x[0])
    means = [mean(row[j] for row in train_x) for j in range(k)]
    sds = [_stdev([row[j] for row in train_x]) or 1.0 for j in range(k)]
    transformed = [[(row[j] - means[j]) / sds[j] for j in range(k)]
                   for row in train_x]
    if x is None:
        return transformed, means, sds
    return transformed, [(x[j] - means[j]) / sds[j] for j in range(k)], means, sds


def _build_rows(stock_rows: list[dict], benchmark_rows: list[dict], horizon: int,
                consensus_history: list[dict], surprises: list[dict]) -> tuple[list[dict], list[dict]]:
    aligned = align_prices(stock_rows, benchmark_rows)
    if len(aligned) < 300:
        return aligned, []
    stock_rets = [_safe_log_return(a["stock"], b["stock"])
                  for a, b in zip(aligned, aligned[1:])]
    bench_rets = [_safe_log_return(a["benchmark"], b["benchmark"])
                  for a, b in zip(aligned, aligned[1:])]
    excess = [s - b for s, b in zip(stock_rets, bench_rets)]

    rows = []
    first = 63
    last = len(aligned) - horizon - 1
    for idx in range(first, last + 1, SAMPLE_STEP):
        x = _feature_vector(aligned, stock_rets, bench_rets, excess, idx,
                            consensus_history, surprises)
        y = sum(excess[idx:idx + horizon])
        rows.append({"date": aligned[idx]["date"], "idx": idx, "x": x, "y": y})
    return aligned, rows


def _fit_predict(train_rows: list[dict], x: list[float]) -> tuple[float, list[float]]:
    train_x = [r["x"] for r in train_rows]
    z_train, z_x, _, _ = _standardize(train_x, x)
    pairs = list(zip(z_train, [r["y"] for r in train_rows]))
    weights = ridge_fit(pairs, alpha=RIDGE_ALPHA)
    pred = sum(a * b for a, b in zip(z_x, weights))
    residuals = [r["y"] - sum(a * b for a, b in zip(z, weights))
                 for r, z in zip(train_rows, z_train)]
    return pred, residuals


def _evaluate(rows: list[dict], horizon: int) -> dict:
    """Purged expanding-window evaluation; target windows cannot overlap train."""
    preds, actuals, probabilities = [], [], []
    for test_pos in range(MIN_TRAIN_ROWS, len(rows)):
        test = rows[test_pos]
        # Because rows are sampled every SAMPLE_STEP sessions, purge by actual
        # price index rather than row count.
        train = [r for r in rows[:test_pos]
                 if r["idx"] + horizon < test["idx"]]
        if len(train) < MIN_TRAIN_ROWS:
            continue
        pred, residuals = _fit_predict(train, test["x"])
        sigma = _stdev(residuals) or 1e-8
        p = _normal_cdf(pred / sigma)
        preds.append(pred)
        actuals.append(test["y"])
        probabilities.append(p)

    if len(preds) < MIN_EVAL_ROWS:
        return {"status": "INSUFFICIENT_EVALUATION",
                "samples": len(preds), "required": MIN_EVAL_ROWS}
    mae = mean(abs(p - y) for p, y in zip(preds, actuals))
    baseline_mae = mean(abs(y) for y in actuals)
    brier = mean((p - float(y > 0)) ** 2
                 for p, y in zip(probabilities, actuals))
    prior = sum(y > 0 for y in actuals) / len(actuals)
    prior_brier = mean((prior - float(y > 0)) ** 2 for y in actuals)
    hit = mean((p >= 0.5) == (y > 0)
               for p, y in zip(probabilities, actuals))
    return {
        "status": "OK",
        "samples": len(preds),
        "mae_pct": round(100 * mae, 3),
        "zero_alpha_mae_pct": round(100 * baseline_mae, 3),
        "brier_score": round(brier, 4),
        "class_prior_brier_score": round(prior_brier, 4),
        "direction_hit_rate": round(hit, 3),
        "passes": bool(mae < baseline_mae and brier < prior_brier and hit > 0.5),
    }


def forecast_alpha(ticker: str, stock_rows: list[dict], benchmark_ticker: str,
                   benchmark_rows: list[dict], consensus_history: list[dict] | None = None,
                   surprises: list[dict] | None = None) -> dict:
    """Fit direct-horizon excess-return models and return a diagnostic payload."""
    consensus_history = consensus_history or []
    surprises = surprises or []
    aligned = align_prices(stock_rows, benchmark_rows)
    if len(aligned) < 800:
        return {
            "status": "INSUFFICIENT_DATA",
            "model": MODEL_NAME,
            "model_version": MODEL_VERSION,
            "benchmark": benchmark_ticker,
            "reason": f"need 800+ aligned stock/benchmark closes, have {len(aligned)}",
        }

    stock_rets = [_safe_log_return(a["stock"], b["stock"])
                  for a, b in zip(aligned, aligned[1:])]
    bench_rets = [_safe_log_return(a["benchmark"], b["benchmark"])
                  for a, b in zip(aligned, aligned[1:])]
    excess = [s - b for s, b in zip(stock_rets, bench_rets)]
    latest_idx = len(aligned) - 1
    latest_x = _feature_vector(aligned, stock_rets, bench_rets, excess, latest_idx,
                               consensus_history, surprises)

    horizons = {}
    all_pass = True
    for horizon in DIRECT_HORIZONS:
        _, rows = _build_rows(stock_rows, benchmark_rows, horizon,
                              consensus_history, surprises)
        validation = _evaluate(rows, horizon)
        train = [r for r in rows if r["idx"] + horizon < latest_idx]
        if len(train) < MIN_TRAIN_ROWS:
            horizons[str(horizon)] = {
                "status": "INSUFFICIENT_TRAINING", "training_rows": len(train),
                "validation": validation,
            }
            all_pass = False
            continue
        pred, residuals = _fit_predict(train, latest_x)
        sigma = _stdev(residuals) or 1e-8
        p_positive = _normal_cdf(pred / sigma)
        horizons[str(horizon)] = {
            "status": "OK",
            "expected_excess_return_pct": round(100 * (exp(pred) - 1), 3),
            "prob_outperform": round(p_positive, 3),
            "residual_sigma_pct": round(100 * sigma, 3),
            "training_rows": len(train),
            "validation": validation,
        }
        all_pass = all_pass and validation.get("passes", False)

    current_expectations = expectation_features(
        aligned[-1]["date"], consensus_history, surprises)
    return {
        "status": "OK",
        "model": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "target": "cumulative stock-minus-benchmark log return",
        "benchmark": benchmark_ticker,
        "as_of": aligned[-1]["date"],
        "direct_horizons": list(DIRECT_HORIZONS),
        "horizons": horizons,
        "feature_contract": {
            "market": ["stock_mom_5", "stock_mom_20", "stock_mom_63",
                       "excess_mom_5", "excess_mom_20", "excess_mom_63",
                       "realized_vol_21", "realized_vol_63",
                       "benchmark_mom_20", "benchmark_mom_63", "drawdown_63"],
            "expectations": ["eps_revision", "revenue_revision",
                             "latest_eps_surprise", "trailing_4q_eps_surprise",
                             "has_consensus", "has_surprise"],
        },
        "current_expectation_features": {
            "eps_revision": round(current_expectations[0], 6),
            "revenue_revision": round(current_expectations[1], 6),
            "latest_eps_surprise": round(current_expectations[2], 6),
            "trailing_4q_eps_surprise": round(current_expectations[3], 6),
            "has_consensus": bool(current_expectations[4]),
            "has_surprise": bool(current_expectations[5]),
        },
        "promotion": {
            "passed_all_horizons": all_pass,
            "deployed_as_primary": False,
            "kill_criterion": "each direct horizon must beat zero-alpha MAE and class-prior Brier score with >50% direction hit rate out of sample",
            "note": "P0 candidate remains diagnostic until the production promotion contract is extended and the full-universe shadow book confirms edge.",
        },
    }
