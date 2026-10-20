"""Pre-registered volatility-forecast replay: can we forecast how much a stock will move?

The event-strategy and fair-value replays found no reliable edge in the
direction of stock-specific moves. Options pay for being right about the size
of a move instead, so the prerequisite for any options strategy is a
volatility forecast that is better than simple rules. This replay asks only
that; whether the forecast also beats the market's implied volatility needs
option data and is a separate, later test (scripts/probe_ibkr_iv_history.py
measures what IBKR can supply).

At the last session of each month, for every covered stock, using data known
at that close only, forecast the realized volatility of daily log returns
(adjusted closes) over the next 21 sessions with:

  RV21, RV63   trailing realized volatility (the rules of thumb)
  EWMA         exponentially weighted variance, lambda 0.94
  HAR          pooled regression of log future vol on log RV5, RV21, RV63
               (Corsi's heterogeneous autoregressive model)
  HAR_PLUS     HAR plus intraday range (Parkinson) vol, SPY vol, long-run
               vol, the past month's losses, and an expected-earnings term
               built only from past SEC 8-K item 2.02 releases

The regressions are refit every month on stock-months whose 21-session target
window had completed before the decision session (purged), once at least
MIN_TRAIN_MONTHS months exist. Scoring is the QLIKE loss on variance (robust
to noise in the realized proxy); the unit is the decision month (mean loss
difference across stocks), with a Newey-West interval. Development stage
(decisions 2016-2023) is descriptive; the hold-out (2024-01 to 2026-08) runs
once, after REGISTERED_PROTOCOL_SHA256 is committed. Nothing here trades.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

from ..agent_intelligence import inference
from ..agent_intelligence.technical_setups import normalized
from ..agents.contracts import digest
from ..market_calendar import session_dates

HORIZON = 21
ANNUAL = 252
EWMA_LAMBDA = 0.94
MIN_TRAIN_MONTHS = 24
TRAIN_START = "2013-01"     # stock-months before the development stage are training data only
HAC_LAG = 2
DEV = ("2016-01", "2023-12")
HOLDOUT = ("2024-01", "2026-08")
Z_FAMILY = 2.497705       # two-sided 99.375%: four hypotheses at family-wise 95% (Bonferroni)
EARNINGS_WINDOW_DAYS = 30  # calendar days spanned by 21 sessions
EARNINGS_HISTORY = 8       # past releases averaged for the reaction-size term
# Committed after the development stage has been reviewed; until then the hold-out refuses.
REGISTERED_PROTOCOL_SHA256 = None
MODELS = ("RV21", "RV63", "EWMA", "HAR", "HAR_PLUS")
HAR_FEATURES = ("lrv5", "lrv21", "lrv63")
PLUS_FEATURES = HAR_FEATURES + ("lpk21", "lspy21", "lrv252", "loss21", "earn", "earn_size")
HYPOTHESES = {
    "H1": ("HAR", "RV21", "all", "the standard model beats last month's volatility (sanity check)"),
    "H2": ("HAR_PLUS", "HAR", "all", "our added inputs improve on the standard model (primary)"),
    "H3": ("HAR_PLUS", "RV21", "all", "our model beats last month's volatility"),
    "H4": ("HAR_PLUS", "HAR", "earnings", "our added inputs improve the forecast in months with an expected "
                                          "earnings release"),
}
EARNINGS_PATH = "docs/evidence/earnings-announcements-v1.json"
EARNINGS_SHA256 = "6937f99aaaf97eb9405b7ca170d07f4ce474e6a5dc94d48463762df060fab4f5"

PROTOCOL = {
    "protocol_id": "volatility-forecast.v1",
    "question": "does a point-in-time model forecast a stock's realized volatility over the next 21 sessions better "
                "than simple rules; a prerequisite for, not a test of, an options edge",
    "universe": "covered agent universe (54 stocks)",
    "decision_sessions": "last XNYS session of each calendar month",
    "target": f"sqrt({ANNUAL}/{HORIZON} * sum of squared daily log returns of the adjusted close) over the "
              f"{HORIZON} sessions after the decision session; every close in the window must exist",
    "models": {
        "RV21": "trailing 21-session realized volatility", "RV63": "trailing 63-session realized volatility",
        "EWMA": f"exponentially weighted variance of daily log returns, lambda {EWMA_LAMBDA}, over 252 sessions",
        "HAR": "pooled OLS of log target volatility on " + ", ".join(HAR_FEATURES),
        "HAR_PLUS": "pooled OLS of log target volatility on " + ", ".join(PLUS_FEATURES),
    },
    "features": {
        "lrvN": "log of trailing N-session realized volatility",
        "lpk21": "log Parkinson volatility from the last 21 normalized (adjusted) high/low ranges",
        "lspy21": "log of SPY's trailing 21-session realized volatility",
        "loss21": "min(0, 21-session log return)",
        "earn": f"1 when a release is expected in the next {EARNINGS_WINDOW_DAYS} calendar days: the company "
                "released in the same window one year earlier (SEC 8-K item 2.02, pinned dataset, past events only)",
        "earn_size": f"earn x log(1 + mean absolute two-session reaction of the last {EARNINGS_HISTORY} past "
                     "releases, in units of the 63-session volatility before each)",
    },
    "fitting": f"refit at every decision month on all stock-months from {TRAIN_START} whose target window ended on "
               f"or before the decision session; evaluated only once {MIN_TRAIN_MONTHS} months of training data "
               "exist and only in the stage's months; variance "
               "forecast exp(2*mu + 2*s2) with s2 the training residual variance of log volatility",
    "loss": "QLIKE on annualized variance: RV/F - ln(RV/F) - 1 (primary); squared error of log volatility "
            "(reported)",
    "unit": "decision month: mean across stocks of loss(benchmark) - loss(model); positive favours the model",
    "interval": f"Newey-West lag {HAC_LAG} on monthly values; Student t(n-1)",
    "hypotheses": {k: {"model": v[0], "benchmark": v[1], "sample": v[2], "statement": v[3]}
                   for k, v in HYPOTHESES.items()},
    "family": {"hypotheses": list(HYPOTHESES), "z": Z_FAMILY,
               "test": "hold-out monthly mean > 0 and its family-wise lower bound > 0"},
    "splits": {"dev": list(DEV), "holdout": list(HOLDOUT), "assignment": "by decision month"},
    "earnings_sha256": EARNINGS_SHA256,
    "use": "evidence for review; never changes trading; beating these baselines does not show an edge over "
           "option-implied volatility",
}
PROTOCOL_SHA256 = digest(PROTOCOL)


# ---- measurements --------------------------------------------------------------

def log_returns(closes: list[float | None]) -> list[float | None]:
    return [None if a is None or b is None else math.log(b / a) for a, b in zip(closes, closes[1:])]


def realized_vol(returns: list[float | None]) -> float | None:
    """Annualized; None when any return in the window is missing."""
    if not returns or any(r is None for r in returns):
        return None
    return math.sqrt(ANNUAL / len(returns) * sum(r * r for r in returns))


def parkinson_vol(bars: list[dict | None]) -> float | None:
    if not bars or any(b is None or b["high"] <= 0 or b["low"] <= 0 for b in bars):
        return None
    return math.sqrt(ANNUAL * mean(math.log(b["high"] / b["low"]) ** 2 for b in bars) / (4 * math.log(2)))


def ewma_vol(returns: list[float | None], lam: float = EWMA_LAMBDA) -> float | None:
    if len(returns) < 63 or any(r is None for r in returns):
        return None
    var = mean(r * r for r in returns[:21])
    for r in returns[21:]:
        var = lam * var + (1 - lam) * r * r
    return math.sqrt(ANNUAL * var)


def qlike(realized_var: float, forecast_var: float) -> float:
    ratio = realized_var / forecast_var
    return ratio - math.log(ratio) - 1


# ---- earnings expectation (past releases only) -----------------------------------

def earnings_events(path=None) -> dict:
    file = Path(path or Path(__file__).resolve().parents[2] / EARNINGS_PATH)
    raw = file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EARNINGS_SHA256:
        raise ValueError("EARNINGS_DATA_CHANGED")
    out = defaultdict(set)
    for e in json.loads(raw)["events"]:
        out[e["ticker"]].add(e["reaction_session"])
    return {t: sorted(v) for t, v in out.items()}


def expected_release(reactions: list[str], decision: str) -> bool:
    """A release fell in the same 30-day window one year earlier (known at the decision)."""
    from datetime import date, timedelta
    d = date.fromisoformat(decision)
    lo, hi = (d - timedelta(days=364)).isoformat(), (d - timedelta(days=364 - EARNINGS_WINDOW_DAYS)).isoformat()
    return any(lo < r <= hi and r <= decision for r in reactions)


# ---- panel ---------------------------------------------------------------------

class Series:
    """One stock's normalized bars aligned to exchange sessions."""

    def __init__(self, rows: list[dict], sessions: list[str]):
        bars = normalized(rows, sessions[-1])
        self.sessions = sessions
        self.index = {s: i for i, s in enumerate(sessions)}
        self.bars = [bars.get(s) for s in sessions]
        self.returns = [None] + log_returns([b["close"] if b else None for b in self.bars])

    def trailing(self, i: int, n: int) -> list[float | None]:
        return self.returns[i - n + 1:i + 1] if i - n + 1 >= 1 else []

    def future(self, i: int, n: int = HORIZON) -> list[float | None]:
        return self.returns[i + 1:i + 1 + n] if i + n < len(self.sessions) else []


def reaction_sizes(series: Series, reactions: list[str]) -> list[tuple[str, float]]:
    """(reaction session, |two-session log return| / (63-session vol x sqrt(2/252))) for each release."""
    out = []
    for r in reactions:
        i = series.index.get(r)
        if i is None or i < 64 or i + 1 >= len(series.sessions):
            continue
        two = series.returns[i:i + 2]
        before = realized_vol(series.trailing(i - 1, 63))
        if any(x is None for x in two) or not before:
            continue
        out.append((series.sessions[i + 1], abs(sum(two)) / (before * math.sqrt(2 / ANNUAL))))
    return out


def observe(series: Series, spy: Series, i: int, reactions: list[str], sizes: list[tuple[str, float]]) -> dict | None:
    """Features at session i from data through its close only; None if a window is incomplete."""
    rv = {n: realized_vol(series.trailing(i, n)) for n in (5, 21, 63, 252)}
    pk = parkinson_vol(series.bars[i - 20:i + 1] if i >= 20 else [])
    spy21 = realized_vol(spy.trailing(i, 21))
    ewma = ewma_vol(series.trailing(i, 252))
    if None in rv.values() or not all(rv.values()) or not pk or not spy21 or not ewma:
        return None
    decision = series.sessions[i]
    earn = 1.0 if expected_release(reactions, decision) else 0.0
    past = [z for done, z in sizes if done <= decision][-EARNINGS_HISTORY:]
    return {"lrv5": math.log(rv[5]), "lrv21": math.log(rv[21]), "lrv63": math.log(rv[63]),
            "lrv252": math.log(rv[252]), "lpk21": math.log(pk), "lspy21": math.log(spy21),
            "loss21": min(0.0, sum(series.trailing(i, 21))), "earn": earn,
            "earn_size": earn * math.log(1 + mean(past)) if past else 0.0,
            "RV21": rv[21], "RV63": rv[63], "EWMA": ewma}


def panel(prices: dict, spy_rows: list[dict], earnings: dict, end: str) -> list[dict]:
    """Every stock-month with complete features; the target where its window has completed."""
    sessions = session_dates("2010-01-04", end)
    month_ends = sorted({max(s for s in sessions if s[:7] == m) for m in {s[:7] for s in sessions}})
    spy = Series(spy_rows, sessions)
    rows = []
    for ticker in sorted(prices):
        series = Series(prices[ticker], sessions)
        reactions = earnings.get(ticker, [])
        sizes = reaction_sizes(series, reactions)
        for m in month_ends:
            i = series.index[m]
            if m[:7] < TRAIN_START:
                continue
            x = observe(series, spy, i, reactions, sizes)
            if x is None:
                continue
            target = realized_vol(series.future(i))
            rows.append({"ticker": ticker, "decision": m, "month": m[:7], "x": x, "target": target,
                         "window_end": series.sessions[i + HORIZON] if i + HORIZON < len(sessions) else None})
    return rows


# ---- fitting and forecasting ---------------------------------------------------

def _ols(rows: list[dict], features: tuple) -> tuple[list[float], float] | None:
    import numpy as np
    X = np.array([[1.0] + [r["x"][f] for f in features] for r in rows])
    y = np.array([math.log(r["target"]) for r in rows])
    if len(rows) <= len(features) + 1:
        return None
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    return [float(b) for b in beta], float(resid @ resid / (len(rows) - len(features) - 1))


def forecasts(rows: list[dict]) -> list[dict]:
    """Each stock-month with a completed target gets every model's variance forecast (when trainable)."""
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["decision"]].append(r)
    out = []
    for decision in sorted(by_month):
        train = [r for r in rows if r["target"] and r["window_end"] and r["window_end"] <= decision]
        months = {r["month"] for r in train}
        fits = {}
        if len(months) >= MIN_TRAIN_MONTHS:
            for name, feats in (("HAR", HAR_FEATURES), ("HAR_PLUS", PLUS_FEATURES)):
                fits[name] = _ols(train, feats)
        for r in by_month[decision]:
            if not r["target"]:
                continue
            f = {name: r["x"][name] ** 2 for name in ("RV21", "RV63", "EWMA")}
            for name, feats in (("HAR", HAR_FEATURES), ("HAR_PLUS", PLUS_FEATURES)):
                fit = fits.get(name)
                if fit:
                    beta, s2 = fit
                    mu = beta[0] + sum(b * r["x"][k] for b, k in zip(beta[1:], feats))
                    f[name] = math.exp(2 * mu + 2 * s2)
            out.append({**{k: r[k] for k in ("ticker", "decision", "month")}, "earn": r["x"]["earn"],
                        "realized_var": r["target"] ** 2, "forecast_var": f, "trained_months": len(months)})
    return out


# ---- evaluation ----------------------------------------------------------------

def split_of(month: str) -> str | None:
    if DEV[0] <= month <= DEV[1]:
        return "dev"
    if HOLDOUT[0] <= month <= HOLDOUT[1]:
        return "holdout"
    return None


def interval(values: list[float], z: float | None = None) -> dict:
    n = len(values)
    if n < 3:
        return {"months": n, "mean": mean(values) if values else None, "lower": None, "upper": None}
    m = mean(values)
    se = inference.newey_west_se(values, lag=HAC_LAG)
    t = inference.t_quantile(n - 1, z) if z else inference.t_quantile_975(n - 1)
    return {"months": n, "mean": m, "newey_west_se": se, "lower": m - t * se, "upper": m + t * se}


def evaluate(scored: list[dict], *, stage: str) -> dict:
    splits = ("dev",) if stage == "dev" else ("dev", "holdout")
    usable = [s for s in scored if all(m in s["forecast_var"] for m in MODELS)]
    out = {"protocol_id": PROTOCOL["protocol_id"], "protocol_sha256": PROTOCOL_SHA256, "stage": stage,
           "first_evaluated_month": min((s["month"] for s in usable), default=None), "splits": {}}
    for split in splits:
        rows = [s for s in usable if split_of(s["month"]) == split]
        losses = {m: [qlike(s["realized_var"], s["forecast_var"][m]) for s in rows] for m in MODELS}
        log_err = {m: [(0.5 * math.log(s["forecast_var"][m] / s["realized_var"])) ** 2 for s in rows] for m in MODELS}
        summary = {"stock_months": len(rows), "months": len({s["month"] for s in rows}),
                   "mean_qlike": {m: mean(v) if v else None for m, v in losses.items()},
                   "mean_sq_log_error": {m: mean(v) if v else None for m, v in log_err.items()},
                   "forecast_to_realized": {m: (mean(s["forecast_var"][m] for s in rows) /
                                                mean(s["realized_var"] for s in rows)) if rows else None
                                            for m in MODELS},
                   "hypotheses": {}}
        for key, (model, bench, sample, statement) in HYPOTHESES.items():
            chosen = [s for s in rows if sample == "all" or s["earn"] == 1.0]
            by_month = defaultdict(list)
            for s in chosen:
                by_month[s["month"]].append(qlike(s["realized_var"], s["forecast_var"][bench]) -
                                            qlike(s["realized_var"], s["forecast_var"][model]))
            values = [mean(v) for _, v in sorted(by_month.items())]
            confirm = split == "holdout"
            stat = interval(values, Z_FAMILY if confirm else None)
            entry = {**stat, "statement": statement, "stock_months": len(chosen)}
            if confirm:
                entry["verdict"] = ("SUPPORTED" if stat["lower"] is not None and stat["mean"] > 0 and stat["lower"] > 0
                                    else "NOT_SUPPORTED")
            summary["hypotheses"][key] = entry
        out["splits"][split] = summary
    return out


def holdout_ready() -> tuple[bool, str]:
    if REGISTERED_PROTOCOL_SHA256 is None:
        return False, "PROTOCOL_NOT_REGISTERED_FOR_HOLDOUT"
    if REGISTERED_PROTOCOL_SHA256 != PROTOCOL_SHA256:
        return False, "PROTOCOL_CHANGED_SINCE_REGISTRATION"
    return True, "READY"


def run(conn, *, tickers: list[str], stage: str, end: str) -> dict:
    from .. import db
    if stage not in ("dev", "holdout"):
        raise ValueError("VOLATILITY_REPLAY_STAGE_INVALID")
    if stage == "holdout":
        ready, reason = holdout_ready()
        if not ready:
            return {"protocol_id": PROTOCOL["protocol_id"], "stage": stage, "status": "REFUSED", "reason": reason}
    prices = {t: db.fetch_prices(conn, t) for t in tickers}
    rows = panel(prices, db.fetch_prices(conn, "SPY"), earnings_events(), end)
    scored = forecasts(rows)
    if stage == "dev":
        scored = [s for s in scored if split_of(s["month"]) == "dev"]
    return {"protocol": PROTOCOL, "result": evaluate(scored, stage=stage), "rows": scored}
