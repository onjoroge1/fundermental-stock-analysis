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
  HAR          pooled regression of log future vol on log RV5, RV21, RV63 and
               the stock's own long-run (252-session) vol: Corsi's
               heterogeneous autoregressive model with a stock-specific level
  HAR_PLUS     HAR plus intraday range (Parkinson) vol, SPY vol, the past
               month's losses, and an expected-earnings term built only from
               past SEC 8-K item 2.02 releases

The regressions are refit every month on stock-months whose 21-session target
window had completed before the decision session (purged). Training starts
when the earnings expectation is computable (the pinned release data begins
2015-06, and the rule looks one year back). Scoring is the QLIKE loss on
variance; the unit is the decision month (mean loss difference across
stocks); inference is a block-means t-test on BLOCKS contiguous blocks of
months, which stays valid when volatility regimes persist. Development stage
(2016-2023) is descriptive; the hold-out (2024-01 to 2026-08, labelled
pseudo-out-of-sample) runs once, after REGISTERED_PROTOCOL_SHA256 is
committed, and only while the forecasting code reproduces the behaviour pin
and the universe is the registered one. Nothing here trades.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

from ..agent_intelligence.technical_setups import normalized
from ..agents.contracts import AGENT_UNIVERSE, digest
from ..market_calendar import session_dates

HORIZON = 21
ANNUAL = 252
EWMA_LAMBDA = 0.94
MIN_TRAIN_MONTHS = 12
TRAIN_START = "2016-06"     # earnings data starts 2015-06-01; the expectation looks one year back
DEV = ("2016-01", "2023-12")
HOLDOUT = ("2024-01", "2026-08")
BLOCKS = 8
# Exact Student-t quantiles for BLOCKS - 1 = 7 degrees of freedom.
T7_975 = 2.364624          # two-sided 95%
T7_FAMILY = 2.841244       # two-sided 97.5%: two confirmatory hypotheses at family-wise 95% (Bonferroni)
MISSING_SHARE = 0.10       # a feature window may lack up to this share of its returns
EARNINGS_WINDOW_DAYS = 30  # calendar days spanned by 21 sessions
EARNINGS_HISTORY = 8       # past releases averaged for the reaction-size term
# Committed after the development stage was reviewed (docs/evidence/volatility-forecast-dev-2026-10-20.md);
# the hold-out runs once against exactly this protocol, forecasting code and universe.
REGISTERED_PROTOCOL_SHA256 = "63fbb13b99b8b3270e570bdfbb08a25456bd36b8befad8f8d1257fe0bb93449c"
MODELS = ("RV21", "RV63", "EWMA", "HAR", "HAR_PLUS")
HAR_FEATURES = ("lrv5", "lrv21", "lrv63", "lrv252")
PLUS_FEATURES = HAR_FEATURES + ("lpk21", "lspy21", "loss21", "earn", "earn_size")
HYPOTHESES = {
    "H1": ("HAR", "RV21", "all", False, "the standard model beats last month's volatility (sanity check, not in "
                                       "the confirmatory family)"),
    "H2": ("HAR_PLUS", "HAR", "all", True, "our added inputs (range, market, losses, earnings) improve on the "
                                          "standard model (primary)"),
    "H4": ("HAR_PLUS", "HAR", "earnings", True, "our added inputs improve the forecast in months with an expected "
                                               "earnings release"),
}
CONFIRMATORY = tuple(k for k, v in HYPOTHESES.items() if v[3])
EARNINGS_PATH = "docs/evidence/earnings-announcements-v1.json"
EARNINGS_SHA256 = "6937f99aaaf97eb9405b7ca170d07f4ce474e6a5dc94d48463762df060fab4f5"
# The forecasting code's output on a fixed synthetic panel (behavior_digest()); registered with
# the protocol, so a later change to the code cannot pass the hold-out gate unnoticed.
BEHAVIOR_SHA256 = "435a839c39c65fed7296842fcbd2ab2731317ec79c70bd8049c7b8ef25425fff"


def _protocol() -> dict:
    return {
        "protocol_id": "volatility-forecast.v1",
        "question": "does a point-in-time model forecast a stock's realized volatility over the next 21 sessions "
                    "better than simple rules; a prerequisite for, not a test of, an options edge",
        "universe": sorted(AGENT_UNIVERSE),
        "decision_sessions": "last XNYS session of each calendar month",
        "target": f"sqrt({ANNUAL}/{HORIZON} * sum of squared daily log returns of the adjusted close) over the "
                  f"{HORIZON} sessions after the decision session; every return in the window must exist",
        "models": {
            "RV21": "trailing 21-session realized volatility", "RV63": "trailing 63-session realized volatility",
            "EWMA": f"exponentially weighted variance of daily log returns, lambda {EWMA_LAMBDA}, over 252 sessions",
            "HAR": "pooled OLS (one intercept; each stock's level enters through lrv252) of log target volatility "
                   "on " + ", ".join(HAR_FEATURES),
            "HAR_PLUS": "pooled OLS of log target volatility on " + ", ".join(PLUS_FEATURES),
        },
        "features": {
            "lrvN": f"log of trailing N-session realized volatility; a window may lack up to {MISSING_SHARE:.0%} of "
                    "its returns (5-session: none)",
            "lpk21": "log Parkinson volatility from the last 21 normalized (adjusted) high/low ranges (at least 19)",
            "lspy21": "log of SPY's trailing 21-session realized volatility",
            "loss21": "min(0, sum of the last 21 log returns)",
            "earn": f"1 when a release is expected in the next {EARNINGS_WINDOW_DAYS} calendar days: the company "
                    "released in the same window one year earlier (SEC 8-K item 2.02, pinned dataset, past events "
                    "only)",
            "earn_size": f"earn x log(1 + mean absolute two-session reaction of the last {EARNINGS_HISTORY} past "
                         "releases, in units of the 63-session volatility before each)",
        },
        "fitting": f"refit at every decision month on all stock-months from {TRAIN_START} whose target window ended "
                   f"on or before the decision session; evaluated once {MIN_TRAIN_MONTHS} months of training data "
                   "exist and only in the stage's months; variance forecast exp(2*mu + 2*s2) with s2 the training "
                   "residual variance of log volatility",
        "loss": "QLIKE on annualized variance: RV/F - ln(RV/F) - 1 (primary); squared error of the log volatility "
                "forecast (mu for the regressions, log of the trailing measure otherwise) is reported",
        "unit": "decision month: mean across stocks of loss(benchmark) - loss(model); positive favours the model",
        "inference": f"block-means t-test: the stage's months split into {BLOCKS} contiguous blocks of near-equal "
                     f"length; t on the {BLOCKS} block means with t({BLOCKS - 1}) critical values (robust to "
                     "persistent volatility regimes)",
        "hypotheses": {k: {"model": v[0], "benchmark": v[1], "sample": v[2], "confirmatory": v[3],
                           "statement": v[4]} for k, v in HYPOTHESES.items()},
        "family": {"hypotheses": list(CONFIRMATORY), "t_critical": T7_FAMILY,
                   "test": "hold-out mean of block means > 0 and its family-wise lower bound > 0"},
        "splits": {"dev": list(DEV), "holdout": list(HOLDOUT), "assignment": "by decision month",
                   "holdout_label": "PSEUDO_OUT_OF_SAMPLE"},
        "earnings_sha256": EARNINGS_SHA256,
        "behavior_sha256": BEHAVIOR_SHA256,
        "caveats": [
            "The hold-out years' prices were already used by earlier replays (not for volatility forecasting); "
            "treat it as pseudo-out-of-sample.",
            "The universe is today's coverage list: survivorship-biased.",
            "Beating these baselines does not show an edge over option-implied volatility.",
        ],
        "use": "evidence for review; never changes trading",
    }


PROTOCOL = _protocol()
PROTOCOL_SHA256 = digest(PROTOCOL)


# ---- measurements --------------------------------------------------------------

def log_returns(closes: list[float | None]) -> list[float | None]:
    return [None if a is None or b is None else math.log(b / a) for a, b in zip(closes, closes[1:])]


def realized_vol(returns: list[float | None], *, missing_share: float = 0.0) -> float | None:
    """Annualized from the returns present; None when more than the allowed share is missing."""
    present = [r for r in returns if r is not None]
    if not returns or len(returns) - len(present) > int(len(returns) * missing_share):
        return None
    return math.sqrt(ANNUAL / len(present) * sum(r * r for r in present))


def parkinson_vol(bars: list[dict | None], minimum: int = 19) -> float | None:
    usable = [b for b in bars if b is not None and b["high"] > 0 and b["low"] > 0 and b["high"] >= b["low"]]
    if len(usable) < minimum:
        return None
    return math.sqrt(ANNUAL * mean(math.log(b["high"] / b["low"]) ** 2 for b in usable) / (4 * math.log(2)))


def ewma_vol(returns: list[float | None], lam: float = EWMA_LAMBDA) -> float | None:
    present = [r for r in returns if r is not None]
    if len(returns) < 252 or len(returns) - len(present) > int(len(returns) * MISSING_SHARE):
        return None
    var = mean(r * r for r in present[:21])
    for r in present[21:]:
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
    """(second reaction session, |two-session log return| / (63-session vol x sqrt(2/252))) per release."""
    out = []
    for r in reactions:
        i = series.index.get(r)
        if i is None or i < 64 or i + 1 >= len(series.sessions):
            continue
        two = series.returns[i:i + 2]
        before = realized_vol(series.trailing(i - 1, 63), missing_share=MISSING_SHARE)
        if any(x is None for x in two) or not before:
            continue
        out.append((series.sessions[i + 1], abs(sum(two)) / (before * math.sqrt(2 / ANNUAL))))
    return out


def observe(series: Series, spy: Series, i: int, reactions: list[str], sizes: list[tuple[str, float]]):
    """(features at session i from data through its close only, None) or (None, reason)."""
    if series.bars[i] is None:
        return None, "NO_BAR"
    rv = {n: realized_vol(series.trailing(i, n), missing_share=0.0 if n == 5 else MISSING_SHARE)
          for n in (5, 21, 63, 252)}
    if any(not v for v in rv.values()):
        return None, "STOCK_HISTORY"
    pk = parkinson_vol(series.bars[i - 20:i + 1] if i >= 20 else [])
    ewma = ewma_vol(series.trailing(i, 252))
    if not pk or not ewma:
        return None, "STOCK_HISTORY"
    spy21 = realized_vol(spy.trailing(i, 21), missing_share=MISSING_SHARE)
    if not spy21:
        return None, "SPY_HISTORY"
    decision = series.sessions[i]
    earn = 1.0 if expected_release(reactions, decision) else 0.0
    past = [z for done, z in sizes if done <= decision][-EARNINGS_HISTORY:]
    loss = sum(r for r in series.trailing(i, 21) if r is not None)
    return {"lrv5": math.log(rv[5]), "lrv21": math.log(rv[21]), "lrv63": math.log(rv[63]),
            "lrv252": math.log(rv[252]), "lpk21": math.log(pk), "lspy21": math.log(spy21),
            "loss21": min(0.0, loss), "earn": earn,
            "earn_size": earn * math.log(1 + mean(past)) if past else 0.0,
            "RV21": rv[21], "RV63": rv[63], "EWMA": ewma}, None


def panel(prices: dict, spy_rows: list[dict], earnings: dict, end: str, *, start: str = TRAIN_START) -> dict:
    """{"rows": stock-months with features (target where its window completed), "dropped": reason counts}."""
    sessions = session_dates("2014-01-02", end)
    month_ends = sorted({max(s for s in sessions if s[:7] == m) for m in {s[:7] for s in sessions}})
    spy = Series(spy_rows, sessions)
    rows, dropped = [], defaultdict(int)
    for ticker in sorted(prices):
        series = Series(prices[ticker], sessions)
        reactions = earnings.get(ticker, [])
        sizes = reaction_sizes(series, reactions)
        for m in month_ends:
            if m[:7] < start:
                continue
            i = series.index[m]
            x, reason = observe(series, spy, i, reactions, sizes)
            if x is None:
                dropped[reason] += 1
                continue
            future = series.future(i)
            target = realized_vol(future) if future else None
            if future and target is None:
                dropped["TARGET_MISSING_BARS"] += 1
            rows.append({"ticker": ticker, "decision": m, "month": m[:7], "x": x, "target": target,
                         "window_end": series.sessions[i + HORIZON] if i + HORIZON < len(sessions) else None})
    return {"rows": rows, "dropped": dict(dropped)}


# ---- fitting and forecasting ---------------------------------------------------

def _ols(rows: list[dict], features: tuple) -> tuple[list[float], float] | None:
    import numpy as np
    if len(rows) <= len(features) + 1:
        return None
    X = np.array([[1.0] + [r["x"][f] for f in features] for r in rows])
    y = np.array([math.log(r["target"]) for r in rows])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    return [float(b) for b in beta], float(resid @ resid / (len(rows) - len(features) - 1))


def forecasts(rows: list[dict]) -> list[dict]:
    """Each stock-month with a completed target gets every model's variance and log-vol forecast."""
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["decision"]].append(r)
    out = []
    for decision in sorted(by_month):
        scored = [r for r in by_month[decision] if r["target"]]
        if not scored:
            continue
        train = [r for r in rows if r["target"] and r["window_end"] and r["window_end"] <= decision]
        months = {r["month"] for r in train}
        fits = {}
        if len(months) >= MIN_TRAIN_MONTHS:
            for name, feats in (("HAR", HAR_FEATURES), ("HAR_PLUS", PLUS_FEATURES)):
                fits[name] = _ols(train, feats)
        for r in scored:
            var = {name: r["x"][name] ** 2 for name in ("RV21", "RV63", "EWMA")}
            log_vol = {name: math.log(r["x"][name]) for name in ("RV21", "RV63", "EWMA")}
            for name, feats in (("HAR", HAR_FEATURES), ("HAR_PLUS", PLUS_FEATURES)):
                fit = fits.get(name)
                if fit:
                    beta, s2 = fit
                    mu = beta[0] + sum(b * r["x"][k] for b, k in zip(beta[1:], feats))
                    var[name], log_vol[name] = math.exp(2 * mu + 2 * s2), mu
            out.append({**{k: r[k] for k in ("ticker", "decision", "month")}, "earn": r["x"]["earn"],
                        "realized_var": r["target"] ** 2, "forecast_var": var, "forecast_log_vol": log_vol,
                        "trained_months": len(months)})
    return out


# ---- evaluation ----------------------------------------------------------------

def split_of(month: str) -> str | None:
    if DEV[0] <= month <= DEV[1]:
        return "dev"
    if HOLDOUT[0] <= month <= HOLDOUT[1]:
        return "holdout"
    return None


def block_means(values: list[float], blocks: int = BLOCKS) -> list[float]:
    """Means of `blocks` contiguous, near-equal blocks (order preserved)."""
    n = len(values)
    edges = [round(k * n / blocks) for k in range(blocks + 1)]
    return [mean(values[a:b]) for a, b in zip(edges, edges[1:]) if b > a]


def interval(values: list[float], critical: float = T7_975) -> dict:
    """Block-means t interval on monthly values."""
    if len(values) < BLOCKS:
        return {"months": len(values), "mean": mean(values) if values else None, "lower": None, "upper": None}
    means = block_means(values)
    m = mean(means)
    se = math.sqrt(sum((x - m) ** 2 for x in means) / (len(means) - 1) / len(means))
    return {"months": len(values), "blocks": len(means), "mean": m, "block_se": se,
            "lower": m - critical * se, "upper": m + critical * se}


def evaluate(scored: list[dict], *, stage: str, dropped: dict | None = None) -> dict:
    splits = ("dev",) if stage == "dev" else ("dev", "holdout")
    usable = [s for s in scored if all(m in s["forecast_var"] for m in MODELS)]
    out = {"protocol_id": PROTOCOL["protocol_id"], "protocol_sha256": PROTOCOL_SHA256, "stage": stage,
           "first_evaluated_month": min((s["month"] for s in usable), default=None),
           "excluded": {**(dropped or {}), "UNTRAINED": len(scored) - len(usable)}, "splits": {}}
    for split in splits:
        rows = [s for s in usable if split_of(s["month"]) == split]
        losses = {m: [qlike(s["realized_var"], s["forecast_var"][m]) for s in rows] for m in MODELS}
        log_err = {m: [(s["forecast_log_vol"][m] - 0.5 * math.log(s["realized_var"])) ** 2 for s in rows]
                   for m in MODELS}
        summary = {"stock_months": len(rows), "months": len({s["month"] for s in rows}),
                   "mean_qlike": {m: mean(v) if v else None for m, v in losses.items()},
                   "mean_sq_log_error": {m: mean(v) if v else None for m, v in log_err.items()},
                   "forecast_to_realized": {m: (mean(s["forecast_var"][m] for s in rows) /
                                                mean(s["realized_var"] for s in rows)) if rows else None
                                            for m in MODELS},
                   "hypotheses": {}}
        if split == "holdout":
            summary["label"] = "PSEUDO_OUT_OF_SAMPLE"
        for key, (model, bench, sample, confirmatory, statement) in HYPOTHESES.items():
            chosen = [s for s in rows if sample == "all" or s["earn"] == 1.0]
            by_month = defaultdict(list)
            for s in chosen:
                by_month[s["month"]].append(qlike(s["realized_var"], s["forecast_var"][bench]) -
                                            qlike(s["realized_var"], s["forecast_var"][model]))
            values = [mean(v) for _, v in sorted(by_month.items())]
            confirm = split == "holdout" and confirmatory
            stat = interval(values, T7_FAMILY if confirm else T7_975)
            entry = {**stat, "statement": statement, "stock_months": len(chosen), "confirmatory": confirmatory}
            if confirm:
                entry["verdict"] = ("SUPPORTED" if stat["lower"] is not None and stat["mean"] > 0 and stat["lower"] > 0
                                    else "NOT_SUPPORTED")
            summary["hypotheses"][key] = entry
        out["splits"][split] = summary
    return out


# ---- behaviour pin -------------------------------------------------------------

def behavior_fixture():
    """A fixed synthetic panel: four GARCH(1,1) stocks with quarterly release jumps, and SPY."""
    import random
    sessions = session_dates("2014-01-02", "2019-12-31")
    releases = sorted({min(d for d in sessions if d >= f"{y}-{m}-05")
                       for y in range(2014, 2020) for m in ("02", "05", "08", "11")})

    def path(seed, start, alpha=0.08, beta=0.9, omega=2e-6, jumps=()):
        rnd = random.Random(seed)
        var, price, rows = omega / (1 - alpha - beta), start, []
        for day in sessions:
            r = rnd.gauss(0, math.sqrt(var)) + ((0.05 if rnd.random() < 0.5 else -0.05) if day in jumps else 0.0)
            var = omega + alpha * r * r + beta * var
            price *= math.exp(r)
            spread = abs(rnd.gauss(0, math.sqrt(var)))
            rows.append({"date": day, "open": price, "high": price * math.exp(spread),
                         "low": price * math.exp(-spread), "close": price, "adj_close": price, "volume": 1e6})
        return rows
    prices = {f"S{k}": path(100 + k, 30.0 + 10 * k, jumps=set(releases)) for k in range(4)}
    return prices, path(7, 300.0, alpha=0.05, beta=0.93), {t: releases for t in prices}


def behavior_digest() -> str:
    prices, spy, earnings = behavior_fixture()
    built = panel(prices, spy, earnings, "2019-12-31", start="2015-01")
    scored = forecasts(built["rows"])

    def r6(x):
        return None if x is None else round(float(x), 6)
    return digest({"forecasts": [[s["ticker"], s["decision"], {m: r6(v) for m, v in sorted(s["forecast_var"].items())}]
                                 for s in scored],
                   "evaluation": json.loads(json.dumps(evaluate(scored, stage="dev")["splits"]), parse_float=r6),
                   "dropped": built["dropped"]})


def holdout_ready(tickers: list[str]) -> tuple[bool, str]:
    if REGISTERED_PROTOCOL_SHA256 is None:
        return False, "PROTOCOL_NOT_REGISTERED_FOR_HOLDOUT"
    if REGISTERED_PROTOCOL_SHA256 != PROTOCOL_SHA256:
        return False, "PROTOCOL_CHANGED_SINCE_REGISTRATION"
    if behavior_digest() != BEHAVIOR_SHA256:
        return False, "FORECASTING_CODE_CHANGED"
    if sorted(tickers) != PROTOCOL["universe"]:
        return False, "UNIVERSE_NOT_REGISTERED"
    return True, "READY"


def run(conn, *, tickers: list[str], stage: str, end: str) -> dict:
    from .. import db
    if stage not in ("dev", "holdout"):
        raise ValueError("VOLATILITY_REPLAY_STAGE_INVALID")
    if stage == "holdout":
        ready, reason = holdout_ready(tickers)
        if not ready:
            return {"protocol_id": PROTOCOL["protocol_id"], "stage": stage, "status": "REFUSED", "reason": reason}
    prices = {t: db.fetch_prices(conn, t) for t in tickers}
    built = panel(prices, db.fetch_prices(conn, "SPY"), earnings_events(), end)
    scored = forecasts(built["rows"])
    if stage == "dev":
        scored = [s for s in scored if split_of(s["month"]) == "dev"]
    return {"protocol": PROTOCOL, "result": evaluate(scored, stage=stage, dropped=built["dropped"]), "rows": scored}
