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
4. NO BASELINE: pretty test-set overlays prove nothing. Here the LSTM is
   walk-forward validated against a block-bootstrap Monte Carlo baseline;
   if it does not beat the baseline, the UI says so and the baseline leads.

All outputs are labeled: short-horizon equity returns are near-random-walk;
honest probabilities hug 50% and honest fans are wide."""
from __future__ import annotations

import json
import math
import random
from datetime import date
from pathlib import Path

from .config import DATA_DIR

PRED_DIR = DATA_DIR / "predictions"
HORIZONS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
N_PATHS = 500
WINDOW = 40
BLOCK = 21
SEED = 7

try:
    import torch
    import torch.nn as nn
    TORCH_OK = True
except ImportError:  # degrade to bootstrap-only, stated in payload
    TORCH_OK = False


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
    max_start = len(returns) - block
    paths = []
    for _ in range(n_paths):
        path: list[float] = []
        while len(path) < n_days:
            s = rng.randrange(0, max_start)
            path.extend(returns[s:s + block])
        paths.append(path[:n_days])
    return paths


# ---------------- LSTM with Gaussian head ----------------

if TORCH_OK:
    class ReturnLSTM(nn.Module):
        def __init__(self, hidden: int = 24, n_features: int = 2):
            super().__init__()
            self.lstm = nn.LSTM(n_features, hidden, num_layers=2,
                                batch_first=True, dropout=0.1)
            self.mu = nn.Linear(hidden, 1)
            self.log_sigma = nn.Linear(hidden, 1)

        def forward(self, x):
            out, _ = self.lstm(x)
            h = out[:, -1, :]
            return self.mu(h).squeeze(-1), self.log_sigma(h).squeeze(-1)


def train_lstm(feats: list, window: int = WINDOW, epochs: int = 12,
               seed: int = SEED):
    torch.manual_seed(seed)
    xs, ys = make_windows(feats, window)
    X = torch.tensor(xs, dtype=torch.float32)
    Y = torch.tensor(ys, dtype=torch.float32)
    model = ReturnLSTM()
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    n = len(X)
    for _ in range(epochs):
        perm = torch.randperm(n)
        for i in range(0, n, 256):
            idx = perm[i:i + 256]
            mu, log_sigma = model(X[idx])
            sigma = torch.exp(log_sigma).clamp(1e-3, 10.0)
            loss = (0.5 * ((Y[idx] - mu) / sigma) ** 2
                    + torch.log(sigma)).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
    model.eval()
    return model


def lstm_paths(model, recent_feats: list, mean: float, std: float,
               vol_mean: float, vol_std: float, n_days: int,
               n_paths: int = N_PATHS, seed: int = SEED) -> list[list[float]]:
    """Monte Carlo rollout: sample each next return from the predicted
    Gaussian, feed it back with an updated causal vol channel —
    uncertainty compounds honestly."""
    torch.manual_seed(seed)
    x = torch.tensor([recent_feats] * n_paths, dtype=torch.float32)
    # raw-return ring buffer per path for the causal vol channel
    raw_recent = [[f[0] * std + mean for f in recent_feats][-BLOCK:]
                  for _ in range(n_paths)]
    out = torch.empty((n_paths, n_days))
    with torch.no_grad():
        for d in range(n_days):
            mu, log_sigma = model(x)
            sigma = torch.exp(log_sigma).clamp(1e-3, 10.0)
            z_next = torch.normal(mu, sigma)
            out[:, d] = z_next
            vol_z = []
            for p in range(n_paths):
                r = z_next[p].item() * std + mean
                raw_recent[p] = (raw_recent[p] + [r])[-BLOCK:]
                w = raw_recent[p]
                m_ = sum(w) / len(w)
                v = math.sqrt(sum((a - m_) ** 2 for a in w)
                              / max(1, len(w) - 1))
                vol_z.append((v - vol_mean) / (vol_std or 1e-8))
            nxt = torch.stack([z_next, torch.tensor(vol_z)], dim=1)
            x = torch.cat([x[:, 1:, :], nxt.unsqueeze(1)], dim=1)
    return [[v * std + mean for v in row.tolist()] for row in out]


# ---------------- distribution summaries (pure) ----------------

def summarize_paths(paths: list[list[float]], last_price: float) -> dict:
    """Percentile fan + move probabilities per horizon from return paths."""
    out = {"horizons": {}}
    for label, days in HORIZONS.items():
        if days > len(paths[0]):
            continue
        finals = sorted(last_price * math.exp(sum(p[:days])) for p in paths)
        n = len(finals)
        pct = lambda q: round(finals[min(n - 1, int(q * n))], 2)
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
        finals = sorted(last_price * math.exp(sum(p[:d + 1])) for p in paths)
        n = len(finals)
        fan.append({"day": d + 1,
                    "p10": round(finals[int(0.10 * n)], 2),
                    "p25": round(finals[int(0.25 * n)], 2),
                    "p50": round(finals[int(0.50 * n)], 2),
                    "p75": round(finals[int(0.75 * n)], 2),
                    "p90": round(finals[int(0.90 * n)], 2)})
    out["fan"] = fan
    return out


# ---------------- walk-forward validation ----------------

def validate(returns: list[float], n_folds: int = 6,
             eval_days: int = 21) -> dict:
    """Train strictly before each cutoff MINUS a purge gap of WINDOW days
    (no training window may touch evaluation data); score the next 21
    trading days. Metrics: direction hit rate and 80%-interval coverage,
    LSTM vs bootstrap. The verdict decides which model LEADS the UI."""
    folds = []
    min_train = 750
    step = 63
    cut_positions = [len(returns) - eval_days - i * step
                     for i in range(n_folds)]
    for cut in sorted(p for p in cut_positions if p >= min_train):
        train = returns[:cut - WINDOW]  # purge gap
        realized = sum(returns[cut:cut + eval_days])
        fold = {"realized_21d_pct": round((math.exp(realized) - 1) * 100, 2)}
        for name in ("bootstrap", "lstm"):
            if name == "bootstrap":
                paths = bootstrap_paths(train, eval_days, n_paths=300)
            elif TORCH_OK:
                m, s = train_stats(train)
                vm, vs = train_stats(rolling_vol(train))
                feats = make_features(train, m, s, vm, vs)
                model = train_lstm(feats, epochs=8)
                paths = lstm_paths(model, feats[-WINDOW:], m, s, vm, vs,
                                   eval_days, n_paths=300)
            else:
                continue
            sums = sorted(sum(p) for p in paths)
            n = len(sums)
            p_up = sum(1 for x in sums if x > 0) / n
            lo, hi = sums[int(0.10 * n)], sums[int(0.90 * n)]
            fold[name] = {
                "prob_positive": round(p_up, 3),
                "direction_hit": (p_up > 0.5) == (realized > 0),
                "in_80pct_interval": lo <= realized <= hi,
            }
        folds.append(fold)

    def rate(model, key):
        vals = [f[model][key] for f in folds if model in f]
        return round(sum(vals) / len(vals), 3) if vals else None

    result = {
        "folds": folds,
        "n_folds": len(folds),
        "bootstrap": {"direction_hit_rate": rate("bootstrap", "direction_hit"),
                      "interval_80_coverage": rate("bootstrap", "in_80pct_interval")},
        "lstm": ({"direction_hit_rate": rate("lstm", "direction_hit"),
                  "interval_80_coverage": rate("lstm", "in_80pct_interval")}
                 if TORCH_OK else None),
    }
    lstm_hr = (result["lstm"] or {}).get("direction_hit_rate")
    boot_hr = result["bootstrap"]["direction_hit_rate"]
    beats = (lstm_hr is not None and boot_hr is not None
             and lstm_hr > boot_hr)
    result["verdict"] = {
        "kill_criterion": "the LSTM leads the display only if it beats the "
                          "block-bootstrap baseline on walk-forward "
                          "direction hit rate; otherwise the baseline leads",
        "lstm_beats_baseline": beats,
        "primary_model": "lstm" if beats else "bootstrap",
        "note": f"small-sample verdict ({len(folds)} folds) — indicative, "
                "not proof; daily equity returns are near-random-walk and "
                "honest probabilities sit near 50%",
    }
    return result


# ---------------- top-level: forecast one ticker ----------------

def forecast(ticker: str, closes: list[dict],
             force: bool = False) -> dict:
    """closes: [{date, adj_close}] ascending. Cached per (ticker, day)."""
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    cache = PRED_DIR / f"{ticker}_{today}.json"
    if cache.exists() and not force:
        cached = json.loads(cache.read_text())
        # a same-day cache built from OLDER prices is stale (e.g. computed
        # before the daily refresh landed) — rebuild, never serve it
        if (cached.get("status") == "OK" and closes
                and cached.get("as_of") == closes[-1]["date"]):
            return cached

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
        m, s = train_stats(rets)
        vm, vs = train_stats(rolling_vol(rets))
        feats = make_features(rets, m, s, vm, vs)
        pooled = []
        for seed in (SEED, SEED + 1, SEED + 2):  # 3-seed ensemble
            model = train_lstm(feats, seed=seed)
            pooled.extend(lstm_paths(model, feats[-WINDOW:], m, s, vm, vs,
                                     HORIZONS["12m"],
                                     n_paths=N_PATHS // 3, seed=seed))
        models["lstm"] = summarize_paths(pooled, last_price)

    payload = {
        "status": "OK",
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
                    "walk-forward reality — read raw and drift-neutral "
                    "columns as bracketing the honest answer.",
        },
        "methodology": {
            "target": "log-return distribution (never price levels)",
            "lstm": ("2-layer LSTM (torch — TF/Keras unavailable on "
                     "py3.14), 2 channels (return + causal 21d vol), "
                     "Gaussian mu/sigma head, 3-seed ensemble, Monte Carlo "
                     "rollout" if TORCH_OK else
                     "unavailable (torch not installed)"),
            "baseline": f"block bootstrap of real historical returns "
                        f"(block={BLOCK}d, {N_PATHS} paths)",
            "leak_controls": "scaling stats fit on training slices only; "
                             f"walk-forward folds train strictly before "
                             f"each cutoff with a {WINDOW}-day purge gap",
            "calibration": "PENDING: probability calibration (temperature "
                           "scaling) needs more folds than currently "
                           "available — interval coverage is reported "
                           "instead and probabilities are model-implied",
            "limitations": "price-history-only model: knows nothing about "
                           "earnings dates, filings, or fundamentals; "
                           "probabilities are model-implied, not calibrated "
                           "guarantees; not investment advice",
        },
    }
    cache.write_text(json.dumps(payload))
    return payload
