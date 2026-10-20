"""Pre-registered comparison of our volatility forecast with option-implied volatility.

volatility-forecast.v1 (registered 63fbb13b..., hold-out H2 and H4 SUPPORTED)
showed that HAR_PLUS forecasts a stock's next-21-session volatility better
than simple rules. An options edge needs more: the forecast must know
something the options market has not already priced. At each month-end this
replay compares, for the same stock and session:

  IV_CAL     the options market's forecast: IBKR's 30-day at-the-money implied
             volatility, calibrated (pooled regression of log realized vol on
             log IV, refit monthly on completed windows, as the volatility
             replay fits its models)
  HAR_PLUS   our forecast, recomputed with the registered code on IBKR bars
  COMBINED   the same regression on log IV and log HAR_PLUS together

against the realized volatility of the next 21 sessions, and scores a
relative-value variance book: short variance where IV is rich against our
forecast, long where it is cheap, at the IV strike less/plus a cost.

It runs on the owner's machine (scripts/run_implied_vol_replay.py) from IBKR
data fetched there (daily bars and the implied-volatility history); IBKR's
terms restrict redistributing that data, so only aggregate results are
committed. Development stage (2017-06 to 2023-12) is descriptive; the
hold-out (2024-01 to 2026-08) runs once after REGISTERED_PROTOCOL_SHA256 is
committed. Nothing here trades.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

from ..agents.contracts import AGENT_UNIVERSE, digest
from . import volatility_replay as vr

VOL_PROTOCOL_SHA256 = "63fbb13b99b8b3270e570bdfbb08a25456bd36b8befad8f8d1257fe0bb93449c"
COST_VOL_POINTS = 0.01      # one volatility point per side: short at IV - 1, long at IV + 1
TERCILE_MIN_STOCKS = 9
MIN_TRAIN_MONTHS = 12
DEV = ("2017-06", "2023-12")
HOLDOUT = vr.HOLDOUT
REGISTERED_PROTOCOL_SHA256 = None
BEHAVIOR_SHA256 = "2c8564ec4d7d0318bedcf02135c8458ae5ee156d1f91e99b529cf18f51e679d1"
HYPOTHESES = {
    "V1": ("descriptive", "all", "variance premium: the mean short-variance payoff at IV - 1 point on every "
                                 "stock-month (the options market prices more movement than happens)"),
    "V2": ("descriptive", "all", "our forecast against the market's: QLIKE(IV_CAL) - QLIKE(HAR_PLUS)"),
    "V3": ("confirmatory", "all", "our forecast adds information the options market has not priced: "
                                  "QLIKE(IV_CAL) - QLIKE(COMBINED) > 0"),
    "V4": ("confirmatory", "all", "the relative-value variance book (short the third of stocks where IV is "
                                  "richest against our forecast, long the cheapest third) earns more than 0 after "
                                  "costs"),
    "V5": ("descriptive", "earnings", "V3 in months with an expected earnings release"),
    "V6": ("descriptive", "earnings", "V4 in months with an expected earnings release"),
}
CONFIRMATORY = tuple(k for k, v in HYPOTHESES.items() if v[0] == "confirmatory")


def _protocol() -> dict:
    return {
        "protocol_id": "implied-vol-comparison.v1",
        "builds_on": {"protocol_id": "volatility-forecast.v1", "protocol_sha256": VOL_PROTOCOL_SHA256,
                      "behavior_sha256": vr.BEHAVIOR_SHA256},
        "universe": sorted(AGENT_UNIVERSE),
        "inputs": {
            "prices": "IBKR daily bars fetched by scripts/fetch_ibkr_prices.py: TRADES OHLC and volume, "
                      "ADJUSTED_LAST as the adjusted close, 15 years; SPY likewise",
            "implied_volatility": "IBKR OPTION_IMPLIED_VOLATILITY daily close (30-day at-the-money, interpolated "
                                  "from two expiries; vendor-computed) from scripts/probe_ibkr_iv_history.py, at "
                                  "exactly the decision session",
            "earnings": f"pinned SEC 8-K item 2.02 dataset (sha256 {vr.EARNINGS_SHA256})",
            "hashes": "the sha256 of the price and implied-volatility files used are recorded with each result",
        },
        "decisions": "the volatility replay's month-end stock-months with a HAR_PLUS forecast and an implied "
                     "volatility at that session (others are counted, by reason)",
        "models": {
            "IV_CAL": "pooled OLS of log realized vol on log IV",
            "HAR_PLUS": "volatility-forecast.v1 (registered code), its own variance forecast",
            "COMBINED": "pooled OLS of log realized vol on log IV and log HAR_PLUS forecast",
            "fitting": f"IV_CAL and COMBINED refit monthly on stock-months whose window ended on or before the "
                       f"decision; evaluated once {MIN_TRAIN_MONTHS} months of joined training data exist; "
                       "variance forecast exp(2*mu + 2*s2)",
        },
        "loss": "QLIKE on annualized variance",
        "trade_proxy": {
            "payoff": "variance-swap payoff normalized by its strike K: short 1 - RV/K, long RV/K - 1; RV is the "
                      "realized variance over the 21 sessions; uncapped",
            "strike": f"short K = (IV - {COST_VOL_POINTS:g})^2, long K = (IV + {COST_VOL_POINTS:g})^2: one "
                      "volatility point of cost per side",
            "book": f"each month rank stocks by log(HAR_PLUS variance / IV^2); short the lowest third, long the "
                    f"highest third (equal weight within each leg, the two legs added); months with fewer than "
                    f"{TERCILE_MIN_STOCKS} stocks are skipped",
            "limits": "a proxy, not tradable quotes: no bid/ask, skew, term structure or delta hedging are modelled",
        },
        "unit": "decision month: mean across stocks (loss differences), or the book's payoff",
        "inference": f"block-means t-test on {vr.BLOCKS} contiguous blocks, t({vr.BLOCKS - 1}) "
                     "(the volatility replay's)",
        "hypotheses": {k: {"role": v[0], "sample": v[1], "statement": v[2]} for k, v in HYPOTHESES.items()},
        "family": {"hypotheses": list(CONFIRMATORY), "t_critical": vr.T7_FAMILY,
                   "test": "hold-out mean of block means > 0 and its family-wise lower bound > 0"},
        "splits": {"dev": list(DEV), "holdout": list(HOLDOUT), "assignment": "by decision month",
                   "holdout_label": "PSEUDO_OUT_OF_SAMPLE"},
        "behavior_sha256": BEHAVIOR_SHA256,
        "caveats": [
            "Prices and HAR_PLUS's hold-out accuracy were already seen; the comparison with implied volatility was "
            "not. Treat the hold-out as pseudo-out-of-sample.",
            "Implied volatility is IBKR's computed at-the-money 30-day series in today's vintage, not quotes.",
            "The trade proxy ignores bid/ask beyond one volatility point, skew, early exercise and hedging costs; "
            "short-variance payoffs have a heavy left tail.",
            "The universe is today's coverage list: survivorship-biased.",
        ],
        "use": "evidence for review; never changes trading",
    }


PROTOCOL = _protocol()
PROTOCOL_SHA256 = digest(PROTOCOL)


# ---- inputs --------------------------------------------------------------------

def _files_sha(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.name.encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


def load_iv(directory) -> tuple[dict, str]:
    """ticker -> {session: implied vol}, and the sha256 of the files read."""
    paths = [p for p in Path(directory).glob("*.json") if p.name not in ("coverage.json", "summary.json")]
    out = {}
    for path in paths:
        data = json.loads(path.read_text())
        rows = data["series"]["OPTION_IMPLIED_VOLATILITY"]["rows"]
        out[data["ticker"]] = {r["date"]: r["close"] for r in rows if r["close"] and r["close"] > 0}
    return out, _files_sha(paths)


def load_prices(directory) -> tuple[dict, str]:
    paths = [p for p in Path(directory).glob("*.json") if p.name != "summary.json"]
    out = {json.loads(p.read_text())["ticker"]: json.loads(p.read_text())["rows"] for p in paths}
    return out, _files_sha(paths)


# ---- joining and fitting -------------------------------------------------------

def join(prices: dict, spy: list[dict], earnings: dict, iv: dict, end: str) -> dict:
    """Stock-months with HAR_PLUS, IV and a completed target; exclusions counted by reason."""
    built = vr.panel(prices, spy, earnings, end)
    window_end = {(r["ticker"], r["decision"]): r["window_end"] for r in built["rows"]}
    dropped = defaultdict(int, built["dropped"])
    rows = []
    for s in vr.forecasts(built["rows"]):
        if "HAR_PLUS" not in s["forecast_var"]:
            dropped["UNTRAINED"] += 1
            continue
        level = iv.get(s["ticker"], {}).get(s["decision"])
        if not level:
            dropped["NO_IV"] += 1
            continue
        rows.append({"ticker": s["ticker"], "decision": s["decision"], "month": s["month"], "earn": s["earn"],
                     "iv": level, "realized_var": s["realized_var"], "har_var": s["forecast_var"]["HAR_PLUS"],
                     "har_log": s["forecast_log_vol"]["HAR_PLUS"],
                     "window_end": window_end[(s["ticker"], s["decision"])]})
    return {"rows": rows, "dropped": dict(dropped)}


def _fit(train: list[dict], features) -> tuple[list[float], float] | None:
    import numpy as np
    if len(train) <= len(features) + 1:
        return None
    X = np.array([[1.0] + [f(r) for f in features] for r in train])
    y = np.array([0.5 * math.log(r["realized_var"]) for r in train])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    return [float(b) for b in beta], float(resid @ resid / (len(train) - len(features) - 1))


FEATURES = {
    "IV_CAL": (lambda r: math.log(r["iv"]),),
    "COMBINED": (lambda r: math.log(r["iv"]), lambda r: r["har_log"]),
}


def calibrated(rows: list[dict]) -> list[dict]:
    """Each row gains IV_CAL and COMBINED variance forecasts (purged monthly refits), when trainable."""
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["decision"]].append(r)
    out = []
    for decision in sorted(by_month):
        train = [r for r in rows if r["window_end"] and r["window_end"] <= decision]
        if len({r["month"] for r in train}) < MIN_TRAIN_MONTHS:
            continue
        fits = {name: _fit(train, feats) for name, feats in FEATURES.items()}
        if not all(fits.values()):
            continue
        for r in by_month[decision]:
            var = {"HAR_PLUS": r["har_var"], "IV_RAW": r["iv"] ** 2}
            for name, (beta, s2) in fits.items():
                mu = beta[0] + sum(b * f(r) for b, f in zip(beta[1:], FEATURES[name]))
                var[name] = math.exp(2 * mu + 2 * s2)
            out.append({**r, "var": var})
    return out


# ---- the variance book ---------------------------------------------------------

def short_payoff(row: dict) -> float:
    return 1 - row["realized_var"] / max(row["iv"] - COST_VOL_POINTS, 1e-4) ** 2


def long_payoff(row: dict) -> float:
    return row["realized_var"] / (row["iv"] + COST_VOL_POINTS) ** 2 - 1


def book(month_rows: list[dict]) -> float | None:
    if len(month_rows) < TERCILE_MIN_STOCKS:
        return None
    ranked = sorted(month_rows, key=lambda r: (math.log(r["har_var"] / r["iv"] ** 2), r["ticker"]))
    third = len(ranked) // 3
    return mean(short_payoff(r) for r in ranked[:third]) + mean(long_payoff(r) for r in ranked[-third:])


# ---- evaluation ----------------------------------------------------------------

def split_of(month: str) -> str | None:
    if DEV[0] <= month <= DEV[1]:
        return "dev"
    if HOLDOUT[0] <= month <= HOLDOUT[1]:
        return "holdout"
    return None


def _monthly(rows: list[dict], value) -> list[float]:
    by_month = defaultdict(list)
    for r in rows:
        by_month[r["month"]].append(r)
    out = []
    for _, group in sorted(by_month.items()):
        v = value(group)
        if v is not None:
            out.append(v)
    return out


def _qlike_gain(model: str, bench: str):
    return lambda group: mean(vr.qlike(r["realized_var"], r["var"][bench]) - vr.qlike(r["realized_var"], r["var"][model])
                              for r in group)


def evaluate(rows: list[dict], *, stage: str, dropped: dict | None = None, inputs: dict | None = None) -> dict:
    splits = ("dev",) if stage == "dev" else ("dev", "holdout")
    out = {"protocol_id": PROTOCOL["protocol_id"], "protocol_sha256": PROTOCOL_SHA256, "stage": stage,
           "inputs": inputs or {}, "excluded": dropped or {}, "splits": {}}
    measures = {
        "V1": lambda g: mean(short_payoff(r) for r in g),
        "V2": _qlike_gain("HAR_PLUS", "IV_CAL"),
        "V3": _qlike_gain("COMBINED", "IV_CAL"),
        "V4": book,
        "V5": _qlike_gain("COMBINED", "IV_CAL"),
        "V6": book,
    }
    for split in splits:
        chosen = [r for r in rows if split_of(r["month"]) == split]
        models = ("IV_RAW", "IV_CAL", "HAR_PLUS", "COMBINED")
        summary = {"stock_months": len(chosen), "months": len({r["month"] for r in chosen}),
                   "mean_qlike": {m: mean(vr.qlike(r["realized_var"], r["var"][m]) for r in chosen) if chosen else None
                                  for m in models},
                   "forecast_to_realized": {m: (mean(r["var"][m] for r in chosen) /
                                                mean(r["realized_var"] for r in chosen)) if chosen else None
                                            for m in models},
                   "mean_iv_over_realized_vol": mean(r["iv"] / math.sqrt(r["realized_var"]) for r in chosen)
                   if chosen else None,
                   "hypotheses": {}}
        if split == "holdout":
            summary["label"] = "PSEUDO_OUT_OF_SAMPLE"
        for key, (role, sample, statement) in HYPOTHESES.items():
            subset = [r for r in chosen if sample == "all" or r["earn"] == 1.0]
            values = _monthly(subset, measures[key])
            confirm = split == "holdout" and role == "confirmatory"
            stat = vr.interval(values, vr.T7_FAMILY if confirm else vr.T7_975)
            entry = {**stat, "role": role, "statement": statement, "stock_months": len(subset),
                     "worst_month": min(values) if values else None}
            if confirm:
                entry["verdict"] = ("SUPPORTED" if stat["lower"] is not None and stat["mean"] > 0 and stat["lower"] > 0
                                    else "NOT_SUPPORTED")
            summary["hypotheses"][key] = entry
        out["splits"][split] = summary
    return out


# ---- behaviour pin and gates ---------------------------------------------------

def behavior_fixture():
    """Ten synthetic GARCH(1,1) stocks with quarterly releases, SPY, and an implied vol that knows
    half of next month's move and carries a 15% premium."""
    import random
    sessions = vr.session_dates("2014-01-02", "2019-12-31")
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
    prices = {f"S{k}": path(200 + k, 30.0 + 5 * k, omega=1e-6 * (1 + k), jumps=set(releases)) for k in range(10)}
    spy = path(17, 300.0, alpha=0.05, beta=0.93)
    rnd = random.Random(11)
    iv = {}
    for ticker, rows in prices.items():
        series = vr.Series(rows, sessions)
        out = {}
        for i, day in enumerate(sessions):
            past = vr.realized_vol(series.trailing(i, 21), missing_share=0.1)
            ahead = vr.realized_vol(series.future(i)) if i + 21 < len(sessions) else None
            if past and ahead:
                out[day] = (0.5 * past + 0.5 * ahead) * 1.15 * math.exp(rnd.gauss(0, 0.05))
        iv[ticker] = out
    return prices, spy, {t: releases for t in prices}, iv


def behavior_digest() -> str:
    prices, spy, earnings, iv = behavior_fixture()
    joined = join(prices, spy, earnings, iv, "2019-12-31")
    rows = calibrated(joined["rows"])

    def r6(x):
        return None if x is None else round(float(x), 6)
    result = evaluate(rows, stage="dev")["splits"]
    return digest({"rows": [[r["ticker"], r["decision"], {m: r6(v) for m, v in sorted(r["var"].items())}] for r in rows],
                   "evaluation": json.loads(json.dumps(result), parse_float=r6), "dropped": joined["dropped"]})


def holdout_ready(tickers: list[str]) -> tuple[bool, str]:
    if vr.PROTOCOL_SHA256 != VOL_PROTOCOL_SHA256 or vr.behavior_digest() != vr.BEHAVIOR_SHA256:
        return False, "VOLATILITY_REPLAY_CHANGED"
    if REGISTERED_PROTOCOL_SHA256 is None:
        return False, "PROTOCOL_NOT_REGISTERED_FOR_HOLDOUT"
    if REGISTERED_PROTOCOL_SHA256 != PROTOCOL_SHA256:
        return False, "PROTOCOL_CHANGED_SINCE_REGISTRATION"
    if behavior_digest() != BEHAVIOR_SHA256:
        return False, "COMPARISON_CODE_CHANGED"
    if sorted(tickers) != PROTOCOL["universe"]:
        return False, "UNIVERSE_NOT_REGISTERED"
    return True, "READY"


def run(*, prices_dir, iv_dir, stage: str, end: str, tickers: list[str]) -> dict:
    if stage not in ("dev", "holdout"):
        raise ValueError("IMPLIED_VOL_REPLAY_STAGE_INVALID")
    if stage == "holdout":
        ready, reason = holdout_ready(tickers)
        if not ready:
            return {"protocol_id": PROTOCOL["protocol_id"], "stage": stage, "status": "REFUSED", "reason": reason}
    prices, prices_sha = load_prices(prices_dir)
    iv, iv_sha = load_iv(iv_dir)
    missing = sorted(set(tickers) - set(prices)) + ([] if "SPY" in prices else ["SPY"])
    if missing:
        return {"protocol_id": PROTOCOL["protocol_id"], "stage": stage, "status": "REFUSED",
                "reason": "PRICES_MISSING", "missing": missing}
    joined = join({t: prices[t] for t in tickers}, prices["SPY"], vr.earnings_events(), iv, end)
    rows = calibrated(joined["rows"])
    joined["dropped"]["CALIBRATION_UNTRAINED"] = len(joined["rows"]) - len(rows)
    if stage == "dev":
        rows = [r for r in rows if split_of(r["month"]) == "dev"]
    inputs = {"prices_sha256": prices_sha, "implied_vol_sha256": iv_sha, "end": end}
    return {"protocol": PROTOCOL, "result": evaluate(rows, stage=stage, dropped=joined["dropped"], inputs=inputs),
            "rows": rows}
