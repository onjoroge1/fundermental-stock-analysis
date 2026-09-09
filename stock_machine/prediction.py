"""Probabilistic price forecaster (the /stock_analysis 'prediction lab').

This is deliberately NOT the Kaggle-notebook design it replaces. That
notebook family has four disqualifying defects, each corrected here:

1. LEAKAGE: MinMaxScaler fit on the full series (test data shapes the
   transform). Here every statistic (mean/std) is fit on the TRAINING slice
   only, per walk-forward fold.
2. PRICE-LEVEL TARGETS: predicting tomorrow's price from today's makes the
   model an expensive echo — persistence looks 99% accurate and predicts
   nothing. Here the target is the LOG RETURN distribution.
3. POINT PREDICTIONS: a single line implies false precision. Here the model
   outputs a distribution (Gaussian head: mu, sigma per step), rolled out by
   Monte Carlo into percentile fans and move probabilities.
4. NO BASELINE: pretty test-set overlays prove nothing. Here drift-bearing
   models face purged walk-forward gates against no-change and historical
   class-prior baselines; drift-neutral leads unless every gate passes.

All outputs are labeled: short-horizon equity returns are near-random-walk;
honest probabilities hug 50% and honest fans are wide."""
from __future__ import annotations

import math
import random

from .forecasts import from_prediction_lab
from .forecasts.calibration import (
    apply_isotonic,
    balanced_accuracy,
    calibration_error,
    fit_isotonic,
    quantile,
)

HORIZONS = {
    "5d": 5, "10d": 10, "20d": 20,
    "1m": 21, "3m": 63, "6m": 126, "12m": 252,
}
VALIDATION_HORIZONS = (5, 10, 20)
N_PATHS = 500
WINDOW = 40
BLOCK = 21
PURGE = 20
SEED = 7
MODEL_VERSION = "forecast-calibration.v4-direct-lstm"
MIN_CALIBRATION_SAMPLES = 10
MIN_EVALUATION_SAMPLES = 12

try:
    import torch
    import torch.nn as nn
    TORCH_OK = True
except ImportError:  # degrade to bootstrap-only, stated in payload
    TORCH_OK = False


def _attach_canonical_contract(payload: dict) -> dict:
    """Add the versioned forecast contract without removing legacy fields."""
    if payload.get("status") == "OK":
        payload["forecast_distribution"] = from_prediction_lab(
            payload
        ).model_dump(mode="json")
    return payload


# ---------------- data prep (pure) ----------------

def log_returns(closes: list[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(closes, closes[1:])
            if a and b and a > 0 and b > 0]


def train_stats(returns: list[float]) -> tuple[float, float]:
    """Mean/std from the given slice ONLY (leak-free by construction)."""
    m = sum(returns) / len(returns)
    var = sum((r - m) ** 2 for r in returns) / max(1, len(returns) - 1)
    return m, math.sqrt(var) or 1e-8


def rolling_vol(returns: list[float], span: int = 21) -> list[float]:
    """Backward-looking rolling std — causal by construction."""
    out = []
    for i in range(len(returns)):
        w = returns[max(0, i - span + 1):i + 1]
        m = sum(w) / len(w)
        out.append(math.sqrt(sum((r - m) ** 2 for r in w) / max(1, len(w) - 1)))
    return out


def make_features(returns: list[float], mean: float, std: float,
                  vol_mean: float, vol_std: float) -> list[list[float]]:
    """Two channels per step: z-scored return + z-scored 21d rolling vol
    (regime awareness). All scaling stats come from the caller's TRAIN slice."""
    vols = rolling_vol(returns)
    return [[(r - mean) / std, (v - vol_mean) / (vol_std or 1e-8)]
            for r, v in zip(returns, vols)]


def make_windows(feats: list, window: int) -> tuple[list, list]:
    """Targets are the NEXT step's return channel — strictly after the window."""
    xs, ys = [], []
    for i in range(len(feats) - window):
        xs.append(feats[i:i + window])
        y = feats[i + window]
        ys.append(y[0] if isinstance(y, list) else y)
    return xs, ys


# ---------------- baseline: block-bootstrap Monte Carlo ----------------

def bootstrap_paths(returns: list[float], n_days: int, n_paths: int = N_PATHS,
                    block: int = BLOCK, seed: int = SEED) -> list[list[float]]:
    """Resample contiguous blocks of REAL historical returns — preserves
    volatility clustering; invents nothing."""
    rng = random.Random(seed)
    if not returns:
        raise ValueError("bootstrap requires at least one return")
    block = min(block, len(returns))
    max_start = len(returns) - block
    paths = []
    for _ in range(n_paths):
        path: list[float] = []
        while len(path) < n_days:
            # randint is inclusive: the final valid historical block must be
            # eligible for sampling too.
            s = rng.randint(0, max_start)
            path.extend(returns[s:s + block])
        paths.append(path[:n_days])
    return paths


# ---------------- direct-horizon LSTM with Gaussian heads ----------------

def make_direct_windows(feats: list, window: int = WINDOW) -> tuple[list, list]:
    """All three cumulative targets end inside the caller's training slice.

    Return features are standardized daily returns. Summing and dividing by
    sqrt(h) keeps target scales comparable; inference reverses this transform.
    """
    xs, ys = [], []
    for end in range(window, len(feats) - max(VALIDATION_HORIZONS) + 1):
        xs.append(feats[end - window:end])
        ys.append([sum(row[0] for row in feats[end:end + h]) / math.sqrt(h)
                   for h in VALIDATION_HORIZONS])
    return xs, ys


if TORCH_OK:
    class ReturnLSTM(nn.Module):
        def __init__(self, hidden: int = 24, n_features: int = 2):
            super().__init__()
            self.lstm = nn.LSTM(n_features, hidden, num_layers=2,
                                batch_first=True, dropout=0.1)
            self.mu = nn.Linear(hidden, len(VALIDATION_HORIZONS))
            self.log_sigma = nn.Linear(hidden, len(VALIDATION_HORIZONS))

        def forward(self, x):
            out, _ = self.lstm(x)
            h = out[:, -1, :]
            return self.mu(h), self.log_sigma(h).clamp(-7.0, 2.3)


def train_lstm(feats: list, window: int = WINDOW, epochs: int = 12,
               seed: int = SEED):
    if not TORCH_OK:
        raise RuntimeError("torch is required for LSTM training")
    torch.manual_seed(seed)
    xs, ys = make_direct_windows(feats, window)
    if not xs:
        raise ValueError("insufficient history for direct horizon targets")
    X = torch.tensor(xs, dtype=torch.float32)
    Y = torch.tensor(ys, dtype=torch.float32)
    model = ReturnLSTM()
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    for _ in range(epochs):
        perm = torch.randperm(len(X))
        for i in range(0, len(X), 256):
            idx = perm[i:i + 256]
            mu, log_sigma = model(X[idx])
            sigma = torch.exp(log_sigma).clamp(1e-3, 10.0)
            loss = (0.5 * ((Y[idx] - mu) / sigma) ** 2
                    + torch.log(sigma)).mean()
            if not torch.isfinite(loss):
                raise ValueError("non-finite LSTM training loss")
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
    model.eval()
    return model


def lstm_horizon_samples(model, recent_feats: list, mean: float, std: float,
                         n_samples: int = N_PATHS, seed: int = SEED) -> dict:
    """One forward pass; marginal terminal returns, never synthetic daily paths."""
    torch.manual_seed(seed)
    with torch.no_grad():
        mu, log_sigma = model(torch.tensor([recent_feats], dtype=torch.float32))
        samples = torch.normal(mu.expand(n_samples, -1),
                               log_sigma.exp().clamp(1e-3, 10).expand(n_samples, -1))
    if not torch.isfinite(samples).all():
        raise ValueError("non-finite LSTM forecast")
    return {h: [float(z) * std * math.sqrt(h) + mean * h
                for z in samples[:, i].tolist()]
            for i, h in enumerate(VALIDATION_HORIZONS)}


def direct_lstm_samples(train: list[float], observed: list[float],
                         n_samples: int = N_PATHS, epochs: int = 12) -> dict:
    """Fit only on train; observed ends at the forecast origin (no outcomes)."""
    m, s = train_stats(train)
    vm, vs = train_stats(rolling_vol(train))
    feats = make_features(train, m, s, vm, vs)
    recent = make_features(observed, m, s, vm, vs)[-WINDOW:]
    pooled = {h: [] for h in VALIDATION_HORIZONS}
    for seed in (SEED, SEED + 1, SEED + 2):
        model = train_lstm(feats, epochs=epochs, seed=seed)
        draws = lstm_horizon_samples(model, recent, m, s,
                                     n_samples=max(1, n_samples // 3), seed=seed)
        for h in pooled:
            pooled[h].extend(draws[h])
    return pooled


def summarize_direct_samples(samples: dict, last_price: float) -> dict:
    summary = {"horizons": {}, "fan": [], "direct_horizon_output": True}
    for days, draws in samples.items():
        # Reuse terminal statistics without interpolating a daily price path.
        finals = [last_price * math.exp(r) for r in draws]
        row = {"days": days,
               **{f"p{q}": round(quantile(finals, q / 100), 2)
                  for q in (10, 25, 50, 75, 90)},
               "prob_positive": round(sum(r > 0 for r in draws) / len(draws), 3)}
        for key, threshold, up in (("prob_up_10pct", 1.1, True),
                                   ("prob_down_10pct", .9, False),
                                   ("prob_down_20pct", .8, False)):
            row[key] = round(sum((v > last_price * threshold) if up
                                 else (v < last_price * threshold)
                                 for v in finals) / len(finals), 3)
        summary["horizons"][f"{days}d"] = row
        summary["fan"].append({"day": days,
                               **{k: v for k, v in row.items()
                                  if k in ("p10", "p25", "p50", "p75", "p90")}})
    return summary


# ---------------- distribution summaries (pure) ----------------

def summarize_paths(paths: list[list[float]], last_price: float) -> dict:
    """Percentile fan + move probabilities per horizon from return paths."""
    out = {"horizons": {}}
    for label, days in HORIZONS.items():
        if days > len(paths[0]):
            continue
        finals = [last_price * math.exp(sum(p[:days])) for p in paths]
        n = len(finals)
        pct = lambda q: round(quantile(finals, q), 2)
        out["horizons"][label] = {
            "days": days,
            "p10": pct(0.10), "p25": pct(0.25), "p50": pct(0.50),
            "p75": pct(0.75), "p90": pct(0.90),
            "prob_positive": round(sum(1 for f in finals if f > last_price) / n, 3),
            "prob_up_10pct": round(sum(1 for f in finals if f > last_price * 1.10) / n, 3),
            "prob_down_10pct": round(sum(1 for f in finals if f < last_price * 0.90) / n, 3),
            "prob_down_20pct": round(sum(1 for f in finals if f < last_price * 0.80) / n, 3),
        }
    # daily fan for charting (weekly steps to keep payload small)
    fan = []
    horizon = len(paths[0])
    for d in range(4, horizon, 5):
        finals = [last_price * math.exp(sum(p[:d + 1])) for p in paths]
        fan.append({"day": d + 1,
                    "p10": round(quantile(finals, 0.10), 2),
                    "p25": round(quantile(finals, 0.25), 2),
                    "p50": round(quantile(finals, 0.50), 2),
                    "p75": round(quantile(finals, 0.75), 2),
                    "p90": round(quantile(finals, 0.90), 2)})
    out["fan"] = fan
    return out


def apply_probability_calibration(summary: dict, model_name: str,
                                  validation: dict) -> None:
    """Calibrate supported horizons in-place while preserving raw P(up)."""
    model_validation = validation.get(model_name) or {}
    calibrators = model_validation.get("probability_calibrators") or {}
    for horizon in summary.get("horizons", {}).values():
        raw = float(horizon["prob_positive"])
        horizon["prob_positive_raw"] = raw
        calibrator = calibrators.get(str(horizon["days"]))
        if calibrator:
            horizon["prob_positive"] = round(
                apply_isotonic(raw, calibrator), 3
            )
            horizon["calibration_status"] = "calibrated"
            horizon["calibration_method"] = (
                "walk_forward_isotonic_pava_beta_smoothed"
            )
            horizon["calibration_samples"] = calibrator["sample_size"]
        else:
            horizon["calibration_status"] = "pending"
            horizon["calibration_method"] = None
            horizon["calibration_samples"] = 0


# ---------------- walk-forward validation and calibration ----------------

def _historical_up_probability(returns: list[float], days: int) -> float:
    outcomes = [sum(returns[i:i + days]) > 0
                for i in range(0, len(returns) - days + 1, days)]
    # Laplace smoothing avoids pretending that a finite sample proves 0%/100%.
    return (sum(outcomes) + 1) / (len(outcomes) + 2)


def _fold_score(paths: list[list[float]], realized: float, days: int) -> dict:
    sums = [sum(path[:days]) for path in paths]
    probability_up = sum(value > 0 for value in sums) / len(sums)
    median = quantile(sums, 0.50)
    return {
        "prob_positive": probability_up,
        "median_log_return": median,
        "signed_error": median - realized,
        "absolute_error": abs(median - realized),
        "brier": (probability_up - float(realized > 0)) ** 2,
        "direction_hit": (probability_up >= 0.5) == (realized > 0),
        "in_80pct_interval": (
            quantile(sums, 0.10) <= realized <= quantile(sums, 0.90)
        ),
    }


def _summarize_validation(folds: list[dict], model: str,
                          calibration_folds: list[dict] | None = None) -> dict | None:
    """Summarize evaluation folds and fit calibrators on earlier folds only."""
    observations = [
        (int(days), scores, fold["outcomes"][days]["positive"], fold)
        for fold in folds
        for days, scores in fold.get("models", {}).get(model, {}).items()
    ]
    if not observations:
        return None

    calibration_observations = [
        (int(days), scores, fold["outcomes"][days]["positive"], fold)
        for fold in (calibration_folds or [])
        for days, scores in fold.get("models", {}).get(model, {}).items()
    ]

    calibrators = {}
    for days in VALIDATION_HORIZONS:
        rows = [row for row in calibration_observations if row[0] == days]
        probabilities = [row[1]["prob_positive"] for row in rows]
        outcomes = [row[2] for row in rows]
        if (len(rows) >= MIN_CALIBRATION_SAMPLES
                and len(set(outcomes)) == 2):
            calibrators[str(days)] = fit_isotonic(probabilities, outcomes)

    calibrated_observations = []
    for days, scores, outcome, fold in observations:
        score = dict(scores)
        calibrator = calibrators.get(str(days))
        if calibrator:
            probability = apply_isotonic(score["prob_positive"], calibrator)
            score["prob_positive_raw"] = score["prob_positive"]
            score["prob_positive"] = probability
            score["brier"] = (probability - float(outcome)) ** 2
            score["direction_hit"] = (probability >= 0.5) == outcome
        calibrated_observations.append((days, score, outcome, fold))
    observations = calibrated_observations

    def summarize(rows: list[tuple[int, dict, bool]]) -> dict:
        probabilities = [row[1]["prob_positive"] for row in rows]
        outcomes = [row[2] for row in rows]
        ba = balanced_accuracy(probabilities, outcomes)
        return {
            "samples": len(rows),
            "signed_bias_pct": round(
                100 * sum(row[1]["signed_error"] for row in rows) / len(rows), 3
            ),
            "return_mae_pct": round(
                100 * sum(row[1]["absolute_error"] for row in rows) / len(rows), 3
            ),
            "brier_score": round(
                sum(row[1]["brier"] for row in rows) / len(rows), 4
            ),
            "expected_calibration_error": round(
                calibration_error(probabilities, outcomes), 4
            ),
            "direction_hit_rate": round(
                sum(row[1]["direction_hit"] for row in rows) / len(rows), 3
            ),
            "balanced_accuracy": round(ba, 3) if ba is not None else None,
            "interval_80_coverage": round(
                sum(row[1]["in_80pct_interval"] for row in rows) / len(rows), 3
            ),
        }

    by_horizon = {
        str(days): summarize([row for row in observations if row[0] == days])
        for days in VALIDATION_HORIZONS
    }
    from .backtest.statistics import mean_uncertainty
    for days in VALIDATION_HORIZONS:
        rows = [r for r in observations if r[0] == days]
        by_horizon[str(days)]["paired_mae_advantage"] = mean_uncertainty(
            [r[3]["models"]["no_change"][str(days)]["absolute_error"] - r[1]["absolute_error"] for r in rows],
            lags=1, alpha=0.05 / (2 * len(VALIDATION_HORIZONS)))
        by_horizon[str(days)]["paired_brier_advantage"] = mean_uncertainty(
            [r[3]["models"]["no_change"][str(days)]["brier"] - r[1]["brier"] for r in rows],
            lags=1, alpha=0.05 / (2 * len(VALIDATION_HORIZONS)))
    return {
        **summarize(observations),
        "by_horizon": by_horizon,
        "by_regime": {
            "trend": {
                regime: summarize([row for row in observations
                                   if row[3]["trend_regime"] == regime])
                for regime in ("bull", "bear")
                if any(row[3]["trend_regime"] == regime for row in observations)
            },
            "volatility": {
                regime: summarize([row for row in observations
                                   if row[3]["volatility_regime"] == regime])
                for regime in ("high", "low")
                if any(row[3]["volatility_regime"] == regime for row in observations)
            },
            "earnings_proximity": {
                "status": "unavailable",
                "reason": "price-only input has no point-in-time earnings calendar",
            },
        },
        "probability_calibrators": calibrators,
        "calibration_samples": len(calibration_folds or []),
        "evaluation_samples": len(folds),
    }


def _promotion_checks(candidate: dict | None, no_change: dict) -> dict:
    checks: list[dict] = []
    for days in VALIDATION_HORIZONS:
        model_h = (candidate or {}).get("by_horizon", {}).get(str(days), {})
        base_h = no_change["by_horizon"][str(days)]
        values = {
            "minimum_evaluation_folds": (
                model_h.get("samples", 0) >= MIN_EVALUATION_SAMPLES
            ),
            "mae_beats_no_change": (
                model_h.get("return_mae_pct", math.inf)
                < base_h["return_mae_pct"]
            ),
            "brier_beats_class_prior": (
                model_h.get("brier_score", math.inf) < base_h["brier_score"]
            ),
            "paired_advantages_have_positive_confidence_bounds": all(
                (model_h.get(k) or {}).get("status") == "OK"
                and (model_h[k].get("lower") or 0) > 0
                for k in ("paired_mae_advantage", "paired_brier_advantage")),
            "balanced_accuracy_above_half": (
                (model_h.get("balanced_accuracy") or 0.0) > 0.5
            ),
            "interval_coverage_acceptable": (
                0.70 <= model_h.get("interval_80_coverage", -1.0) <= 0.95
            ),
            "probability_calibrator_fitted": (
                str(days) in (candidate or {}).get("probability_calibrators", {})
            ),
            "direct_horizon_output": candidate is not None and (
                candidate.get("model_name") != "lstm"
                or candidate.get("direct_horizon_output") is True),
        }
        checks.append({"horizon_days": days, **values,
                       "passed": all(values.values())})
    return {"by_horizon": checks,
            "passed": bool(checks) and all(check["passed"] for check in checks)}


def validate(returns: list[float], n_folds: int = 30,
             evaluate_lstm: bool = False, lstm_epochs: int = 12) -> dict:
    """Purged expanding-window validation at 5/10/20 trading days.

    A drift-bearing model may lead only when every horizon beats a no-change
    return baseline and a historical class-prior probability baseline while
    also meeting direction, coverage, and calibration gates.
    """
    if evaluate_lstm and not TORCH_OK:
        raise RuntimeError("install the prediction extra to evaluate LSTM")
    folds = []
    min_train = 750
    max_horizon = max(VALIDATION_HORIZONS)
    # Forty-two sessions keeps 20-day outcomes non-overlapping while yielding
    # enough history to separate calibration from final evaluation.
    step = 42
    cut_positions = [len(returns) - max_horizon - i * step
                     for i in range(n_folds)]
    for fold_number, cut in enumerate(sorted(
            position for position in cut_positions if position - PURGE >= min_train
    )):
        train = returns[:cut - PURGE]
        mean = sum(train) / len(train)
        demeaned = [value - mean for value in train]
        model_paths = {
            "bootstrap": bootstrap_paths(
                train, max_horizon, n_paths=300, seed=SEED + fold_number
            ),
            "bootstrap_drift_neutral": bootstrap_paths(
                demeaned, max_horizon, n_paths=300, seed=SEED + fold_number
            ),
        }
        direct_samples = (direct_lstm_samples(
            train, returns[:cut], n_samples=300, epochs=lstm_epochs
        ) if evaluate_lstm else None)
        historical_vol = rolling_vol(train)
        recent_vol = historical_vol[-1]
        reference_vol = quantile(historical_vol[-252:], 0.50)
        fold = {"cut_index": cut, "purge_days": PURGE,
                "trend_regime": ("bull" if sum(train[-63:]) >= 0 else "bear"),
                "volatility_regime": ("high" if recent_vol >= reference_vol
                                      else "low"),
                "outcomes": {}, "models": {}}
        for days in VALIDATION_HORIZONS:
            realized = sum(returns[cut:cut + days])
            prior = _historical_up_probability(train, days)
            outcome = realized > 0
            fold["outcomes"][str(days)] = {
                "realized_return_pct": round(100 * math.expm1(realized), 3),
                "positive": outcome,
            }
            fold["models"].setdefault("no_change", {})[str(days)] = {
                "prob_positive": prior,
                "median_log_return": 0.0,
                "signed_error": -realized,
                "absolute_error": abs(realized),
                "brier": (prior - float(outcome)) ** 2,
                "direction_hit": (prior >= 0.5) == outcome,
                "in_80pct_interval": False,
            }
            if direct_samples is not None:
                fold["models"].setdefault("lstm", {})[str(days)] = _fold_score(
                    [[value] for value in direct_samples[days]], realized, 1)
            for name, paths in model_paths.items():
                fold["models"].setdefault(name, {})[str(days)] = _fold_score(
                    paths, realized, days
                )
        folds.append(fold)

    # Calibrators see only the older folds. Metrics and promotion gates see
    # only newer folds that did not fit the calibrator.
    if len(folds) >= MIN_CALIBRATION_SAMPLES + MIN_EVALUATION_SAMPLES:
        split = len(folds) - MIN_EVALUATION_SAMPLES
        calibration_folds, evaluation_folds = folds[:split], folds[split:]
    else:
        calibration_folds, evaluation_folds = [], folds
    summaries = {
        name: _summarize_validation(
            evaluation_folds, name,
            calibration_folds if name != "no_change" else None,
        )
        for name in ("no_change", "bootstrap_drift_neutral", "bootstrap")
    }
    summaries["lstm"] = _summarize_validation(
        evaluation_folds, "lstm", calibration_folds) if evaluate_lstm else None
    if summaries["lstm"] is not None:
        summaries["lstm"]["direct_horizon_output"] = True
    for name, summary in summaries.items():
        if summary is not None:
            summary["model_name"] = name
    no_change = summaries["no_change"]
    promotion = {
        name: _promotion_checks(summaries[name], no_change)
        for name in ("bootstrap", "lstm") if summaries[name] is not None
    }
    promoted = next(
        (name for name in ("bootstrap",)
         if promotion.get(name, {}).get("passed")),
        None,
    )
    primary = promoted or "bootstrap_drift_neutral"
    return {
        "folds": folds,
        "n_folds": len(folds),
        "calibration_folds": len(calibration_folds),
        "evaluation_folds": len(evaluation_folds),
        "horizons_days": list(VALIDATION_HORIZONS),
        "purge_days": PURGE,
        **summaries,
        "promotion": promotion,
        "verdict": {
            "primary_model": primary,
            "forecast_edge": promoted is not None,
            "lstm_beats_baseline": promotion.get("lstm", {}).get("passed", False),
            "lstm_role": "diagnostic; separate research evaluation required",
            "kill_criterion": "a drift-bearing model leads only if all 5/10/20-day "
                              "walk-forward gates pass against no-change and "
                              "historical class-prior baselines",
            "note": (f"{len(calibration_folds)} calibration and "
                     f"{len(evaluation_folds)} untouched evaluation folds with "
                     f"a {PURGE}-session purge; no forecast edge means "
                     "drift-neutral leads"),
        },
    }


# ---------------- top-level: forecast one ticker ----------------

def forecast(ticker: str, closes: list[dict],
             force: bool = False) -> dict:
    """Compute one forecast without filesystem or database side effects.

    ``force`` remains as a compatibility argument for existing callers. The
    worker that invokes this function owns persistence; web requests only read
    completed forecast vintages.
    """
    series = [c["adj_close"] for c in closes if c.get("adj_close")]
    if len(series) < 800:
        return {"status": "INSUFFICIENT_DATA",
                "reason": f"need 800+ daily closes, have {len(series)}"}
    rets = log_returns(series)
    last_price = series[-1]

    validation = validate(rets)
    primary = validation["verdict"]["primary_model"]

    models = {}
    boot = bootstrap_paths(rets, HORIZONS["12m"])
    models["bootstrap"] = summarize_paths(boot, last_price)

    # drift-neutral control: identical resampling on DEMEANED returns.
    # Pressure-testing showed the raw fans replay historical drift (corr
    # 0.92 with past CAGR) and run +6-8pt hot on P(up) vs walk-forward
    # reality — the drift-neutral column is the corrective companion.
    m_all = sum(rets) / len(rets)
    demeaned = [r - m_all for r in rets]
    boot_dn = bootstrap_paths(demeaned, HORIZONS["12m"])
    models["bootstrap_drift_neutral"] = summarize_paths(boot_dn, last_price)
    hist_drift_ann_pct = round((math.exp(m_all * 252) - 1) * 100, 2)
    if TORCH_OK:
        models["lstm"] = summarize_direct_samples(
            direct_lstm_samples(rets, rets), last_price)

    for model_name, summary in models.items():
        apply_probability_calibration(summary, model_name, validation)

    payload = {
        "status": "OK",
        "model_version": MODEL_VERSION,
        "ticker": ticker,
        "as_of": closes[-1]["date"],
        "last_price": round(last_price, 2),
        "history_tail": [{"date": c["date"],
                          "close": round(c["adj_close"], 2)}
                         for c in closes[-126:]],
        "primary_model": primary,
        "models": models,
        "validation": validation,
        "drift_diagnostics": {
            "historical_drift_annualized_pct": hist_drift_ann_pct,
            "pup_12m_with_drift": models["bootstrap"]["horizons"]["12m"]["prob_positive"],
            "pup_12m_drift_neutral": models["bootstrap_drift_neutral"]["horizons"]["12m"]["prob_positive"],
            "note": "Raw fans extrapolate this stock's own historical drift "
                    "(survivorship-selected bull-market sample). Universe "
                    "pressure test measured +6-8pt upward bias in P(up) vs "
                    "walk-forward reality. Drift-neutral therefore leads "
                    "unless the raw model passes every promotion gate.",
        },
        "methodology": {
            "target": "log-return distribution (never price levels)",
            "lstm": ("2-layer LSTM (torch — TF/Keras unavailable on "
                     "py3.14), 2 channels (return + causal 21d vol), "
                     "direct 5/10/20-session Gaussian heads, 3-seed ensemble, "
                     "terminal marginal samples" if TORCH_OK else
                     "unavailable (torch not installed)"),
            "baseline": f"block bootstrap of real historical returns "
                        f"(block={BLOCK}d, {N_PATHS} paths)",
            "leak_controls": "scaling stats fit on training slices only; "
                             f"walk-forward folds train strictly before "
                             f"each cutoff with a {PURGE}-day purge gap",
            "calibration": "5/10/20-day P(up) uses beta-smoothed isotonic "
                           "PAVA fitted only on older purged walk-forward "
                           "folds, then scored on untouched newer folds; "
                           f"requires {MIN_CALIBRATION_SAMPLES}+ calibration "
                           f"and {MIN_EVALUATION_SAMPLES}+ evaluation folds "
                           "with both outcome classes; other horizons remain "
                           "pending",
            "limitations": "price-history-only model: knows nothing about "
                           "earnings dates, filings, or fundamentals; "
                           "calibration status is reported per horizon and "
                           "is never a guarantee; direct LSTM output remains "
                           "diagnostic-only; not investment advice",
        },
    }
    _attach_canonical_contract(payload)
    return payload
