"""EXT-FADE.v1 overlays: three frozen ways to size or filter the lab's one confirmed strategy, checked once.

EXT-FADE.v1 (intraday_lab.py) fades a gap of at least 1% that extends yesterday's move of at least 1%, from 09:31 to
the gap fill or the close. It passed its confirm on 2017-07..2019-12. The research round on all of 2016-2019 (about
1,400 evaluations) found no second strategy, only overlays on v1's own trades. Three were frozen on 2026-10-24,
before any 2020 bar or daily value was read:

  EXT-FADE-SW.v1   sizing: weight each v1 trade by a five-flag score S5, S5 / 3.186 (S5 = 0 is no trade)
  EXT-FADE-P2.v1   filter: keep a v1 trade only if yesterday's move was at least 2% (v1 asks 1%)
  EXT-FADE-IVR.v1  filter: keep a v1 trade only if at least 2 of 3 implied-volatility conditions hold at the prior
                   close; plus a secondary, non-gating question: within the price-only stand-ins, does IV >= HV add
                   anything?

Every input is known before the 09:31 entry: the gap (the 09:30 open), the prior session's daily row, the prior
session's minute bars, and the implied and historical volatility at the prior close. A missing input makes its flag
or condition 0. The overlays never change a v1 trade's entry, exit, hedge or score; they weight it or drop it.

The one check (scripts/run_ext_fade_overlays.py) runs on 2020-01..2023-12, after EXT-FADE.v1's own one-time check is
in the lab's log, on exactly the trades the lab replays for that check, and never reads 2024-01 or later. Its pass
bars (PASS_BARS) were fixed with the specs. It refuses unless the module, the runner and the IV/HV files are the
registered ones (docs/evidence/ext-fade-overlays-registration-2026-10-24.json), the lab's code is the code of v1's
check, and none of the three specs has been checked before; its log is written under a lock before it returns.

The lab, the replay and the IV loader are imported read-only. Information only: nothing here trades.
"""
from __future__ import annotations

import bisect
import fcntl
import hashlib
import json
import math
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ..agents.contracts import digest
from . import implied_vol_replay as ivr
from . import intraday_lab as lab
from . import intraday_replay as ir

OVERLAY_ID = "ext-fade-overlays.v1"
BASE = "EXT-FADE.v1"
BASE_SPEC = lab.VARIANTS[BASE]
CALIBRATION = ("2016-01", "2019-12")    # the research round's months: everything before the check
CALIBRATION_LAST_DAY = "2019-12-31"
HALVES = {"H1": ("2016-01", "2017-12"), "H2": ("2018-01", "2019-12")}
CHECK = lab.CHECK                       # 2020-01..2023-12
CHECK_LAST_DAY = "2023-12-31"
YEARS = tuple(f"{y}" for y in range(2020, 2024))
HOLDOUT_FROM = lab.HOLDOUT_FROM
ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / "docs" / "evidence" / "ext-fade-overlays-log.json"
REGISTRATION_PATH = ROOT / "docs" / "evidence" / "ext-fade-overlays-registration-2026-10-24.json"
RUNNER_PATH = ROOT / "scripts" / "run_ext_fade_overlays.py"
PROTECTED = ("stock_machine/backtest/intraday_lab.py", "stock_machine/backtest/intraday_replay.py",
             "scripts/run_intraday_lab.py", "stock_machine/backtest/implied_vol_replay.py")
VARIANTS_THIS_ROUND = 1413              # evaluations in the 2016-2019 research round, on top of the lab log's tries
IV_SERIES, HV_SERIES = "OPTION_IMPLIED_VOLATILITY", "HISTORICAL_VOLATILITY"

# ---- the frozen specs ---------------------------------------------------------------------------------------------

SW, P2, IVR = "EXT-FADE-SW.v1", "EXT-FADE-P2.v1", "EXT-FADE-IVR.v1"
MISSING = ("a flag or condition whose input is missing is 0 (not met): no daily row for the prior session, no atr14, "
           "no implied or historical vol at the prior close, no SPY IV at the prior close or before it, no SPY daily "
           "row for the prior session or the one before, no sigma_d")
ON = {"variant": BASE, "spec": BASE_SPEC, "trades": "the lab's own EXT-FADE.v1 trades (lab.replay), unchanged"}
PRIOR_MOVE = "|prior_close_raw / close_before_prior - 1|, v1's own prior-move quantity"
SPECS = {
    SW: {
        "overlay": "SIZE_BY_SCORE", "on": ON,
        "flags": [
            {"name": "prior_move", "input": PRIOR_MOVE, "at_least": 0.02},
            {"name": "gap", "input": "|09:30 open / dividend-adjusted prior close - 1|, v1's gap", "at_least": 0.015},
            {"name": "prior_range_atr", "input": "(the prior session's daily high - daily low) / atr14, from the "
                                                 "registered daily price rows", "at_least": 0.879},
            {"name": "prior_leg", "input": "(the close of the prior session's last traded minute / the close of its "
                                           "10:29 minute - 1) x sign(gap)", "at_least": 0.0,
             "missing": "0 if the prior session is not one of the run's eligible stock-days (the lab judges "
                        "eligibility only within the run's months, so a run's first session has none) or its 10:29 "
                        "minute did not trade"},
            {"name": "iv", "input": "the stock's 30-day ATM implied vol at the prior close", "at_least": 0.2805},
        ],
        "score": "S5 = the number of flags met (0 to 5)",
        "weight": {"of": "S5 / s_bar", "s_bar": 3.186, "s_bar_is": "the mean S5 over the 1,900 v1 trades of 2016-2019",
                   "zero": "S5 = 0: no trade", "hedge": "the beta x SPY hedge scales with the position"},
        "missing": MISSING,
        "evaluation": "weights are normalised by the evaluated period's own mean weight (equal exposure with v1)",
    },
    P2: {
        "overlay": "FILTER", "on": ON,
        "keep": [{"name": "prior_move", "input": PRIOR_MOVE, "at_least": 0.02}], "min_met": 1,
        "equals": "the lab spec {setup EXTENSION_FADE, gap 0.01, prior 0.02, spy_gap_max None, entry_bar 1, no cap}, "
                  "applied as a subset of v1's trades",
        "missing": MISSING,
    },
    IVR: {
        "overlay": "FILTER", "on": ON,
        "keep": [
            {"name": "iv_over_hv", "input": "iv_prior / hv_prior, the stock's 30-day ATM implied vol over its 30-day "
                                            "historical vol, both at the prior close", "at_least": 1.0},
            {"name": "spy_iv_change", "input": "SPY's 30-day ATM IV at the prior close / SPY's IV at the IV "
                                               "observation before it (dates before the prior session) - 1",
             "above": 0.0},
            {"name": "iv", "input": "iv_prior", "at_least": 0.28},
        ],
        "min_met": 2,
        "missing": MISSING,
        "secondary": {
            "gating": False,
            "pxc": [{"name": "spy_fell", "input": "SPY's adjusted close on the prior session / its adjusted close the "
                                                  "session before - 1", "below": 0.0},
                    {"name": "rv", "input": "sigma_d x sqrt(252)", "at_least": 0.28}],
            "among": "trades with PXC (the number of the two price-only conditions met) at most max_pxc",
            "max_pxc": 1,
            "compare": "mean net of IV >= HV trades minus IV < HV trades (trades with both IV and HV known)",
        },
    },
}
SW_FLAGS = {f["name"]: f["at_least"] for f in SPECS[SW]["flags"]}
S_BAR = SPECS[SW]["weight"]["s_bar"]
P2_PRIOR = SPECS[P2]["keep"][0]["at_least"]
P2_MIN = SPECS[P2]["min_met"]
IVR_COND = {c["name"]: c for c in SPECS[IVR]["keep"]}
IVR_MIN = SPECS[IVR]["min_met"]
PXC = {c["name"]: c for c in SPECS[IVR]["secondary"]["pxc"]}
PXC_MAX = SPECS[IVR]["secondary"]["max_pxc"]

# ---- the pass bars, fixed with the specs ---------------------------------------------------------------------------
# Costs are per side. t is lab.session_t, the leave-one-session-out jackknife; a difference of two means uses the same
# jackknife (each session's trades dropped together, centred on the full-sample estimate as lab.session_t is). Years
# are 2020..2023; a year counts as positive when its mean is above 0. The book Sharpe sums each session's P&L over the
# period's sessions with an eligible stock-day (no-trade sessions count as 0) and is mean / sd x sqrt(252).

PASS_BARS = {
    SW: {
        "standalone": {"of": "v_i = (w_i / w_bar) x net_i over all v1 trades", "cost_bps": 5, "mean_above": 0.0,
                       "t_above": 2.0, "min_positive_years": 3},
        "gain": {"of": "d_i = (w_i / w_bar - 1) x net_i over all v1 trades", "cost_bps": 5, "mean_above": 0.0,
                 "t_at_least": 2.0, "min_positive_years": 3},
        "book_sharpe": {"cost_bps": 5, "at_least": BASE},
        "w_bar": "the check period's mean weight, for evaluation only",
    },
    P2: {
        "kept": {"cost_bps": 10, "mean_above": 0.0, "t_above": 2.0, "min_positive_years": 3,
                 "and_lab_check_bar": "lab.pass_bar(kept, 'check'): 5 bps, mean above 0, t above 2, 3 of 4 years"},
        "kept_minus_removed": {"cost_bps": 10, "mean_above": 0.0, "t_at_least": 2.0},
        "book_sharpe": {"cost_bps": 10, "at_least": BASE},
    },
    IVR: {
        "kept": {"cost_bps": 5, "mean_above": 0.0, "t_above": 2.0, "min_positive_years": 3},
        "kept_minus_removed": {"cost_bps": 5, "mean_above": 0.0, "t_at_least": 2.0, "min_positive_years": 3},
        "book_sharpe": {"cost_bps": 5, "at_least": BASE},
        "secondary": {"cost_bps": 5, "mean_above": 0.0, "t_at_least": 2.0, "gating": False,
                      "if_it_fails": "record that the options data adds nothing over prices here"},
    },
}
STANDALONE = {SW: "standalone", P2: "kept", IVR: "kept"}   # the part that stands alone if v1 fails its own check
VERDICTS = ("An overlay counts only if EXT-FADE.v1 also passes its check. If v1 fails, an overlay that passes its own "
            "standalone part becomes a new strategy and can go forward only as such. Overlays that pass go into the "
            "2024+ hold-out protocol as pre-registered secondary hypotheses to v1, with a Holm correction across them. "
            "A failure is final for these data: any change to a spec is a new variant.")

# ---- the stress tables, reported and not gating ---------------------------------------------------------------------

ENTRY_0932 = f"{BASE}@09:32"
STRESS = {"cost_bps": 5, "costs_bps": (10, 15), "entry_0932": {**BASE_SPEC, "entry_bar": 2},
          "drop_best_sessions": 20, "drop_top_stocks": 3, "trim_sessions": 20, "by_year": True}
BOOKS = ("EXT-FADE.v1", SW, P2, IVR)


def spec_sha(spec_id: str) -> str:
    """The overlay itself, whatever its id or the wording around it: the once-only check's key."""
    return digest(SPECS[spec_id])


def file_sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def module_sha() -> str:
    return file_sha(__file__)


# ---- inputs ------------------------------------------------------------------------------------------------------

def vol_paths(directory) -> list[Path]:
    """The IV/HV files, chosen as implied_vol_replay.load_iv chooses them."""
    return [p for p in Path(directory).glob("*.json") if p.name not in ("coverage.json", "summary.json")]


def load_vols(directory, last_day: str) -> tuple[dict, str]:
    """{"iv": ticker -> {date: vol}, "hv": ...} cut at last_day, and the sha256 of the files (implied_vol_replay's
    _files_sha, so the same files give its registered implied-vol hash). Nothing after last_day is kept."""
    if last_day >= HOLDOUT_FROM:
        raise ValueError("EXT_FADE_OVERLAYS_NEVER_READ_THE_HOLDOUT")
    paths = vol_paths(directory)
    out = {"iv": {}, "hv": {}}
    for path in paths:
        data = json.loads(path.read_text())
        for key, series in (("iv", IV_SERIES), ("hv", HV_SERIES)):
            rows = data["series"][series]["rows"]
            out[key][data["ticker"]] = {r["date"]: r["close"] for r in rows
                                        if r["close"] is not None and r["date"] <= last_day}
    return out, ivr._files_sha(paths)


def cut_prices(prices: dict, last_day: str) -> dict:
    return {t: [r for r in rows if r["date"] <= last_day] for t, rows in prices.items()}


def stock_days(bars_for, prices: dict, months: tuple[str, str], tickers: list[str]) -> dict:
    """The eligible stock-days of a run, judged exactly as lab.replay judges them (the same warm-up and detection
    months, daily features, history and eligibility), with what the overlays read from each: its daily features and
    two minute closes (the 10:29 minute's, None if it did not trade, and the last traded minute's)."""
    if months[0] > months[1]:
        raise ValueError("EXT_FADE_OVERLAYS_MONTHS_INVERTED")
    if months[1] >= HOLDOUT_FROM:
        raise ValueError("EXT_FADE_OVERLAYS_NEVER_READ_THE_HOLDOUT")
    spy_rows = prices["SPY"]
    loaded = [ir.previous_month(months[0])] + ir.months_between(*months)
    detect = ir.detection_months(months)
    spy_seen = {m: bars_for("SPY", m) for m in detect}
    spy_days = {s: d for m in loaded for s, d in spy_seen[m].items()}
    spy_feat, spy_bad = ir.spy_features(spy_rows, {s: d for m in detect for s, d in spy_seen[m].items()})
    features, closes, eligible = {}, {}, defaultdict(int)
    for ticker in tickers:
        rows = prices.get(ticker, [])
        by_month = {month: bars_for(ticker, month) for month in detect}
        feats = ir.daily_features(rows, spy_rows,
                                  ir.off_rows(rows, {s: d for m in detect for s, d in by_month[m].items()}), spy_bad)
        daily = {"first": rows[0]["date"] if rows else None, "dates": {r["date"] for r in rows}}
        hist = ir.History()
        for month in loaded:
            days = by_month[month]
            for session in sorted(days):
                day = days[session]
                if months[0] <= session[:7] <= months[1]:
                    f = feats.get(session)
                    if ir.eligibility(ticker, session, day, f, hist, spy_days.get(session), spy_feat.get(session),
                                      ir.CORPORATE_ACTIONS.get(ticker, ()), daily) is None:
                        eligible[session] += 1
                        features[(ticker, session)] = {
                            "prior_date": f["prior_date"], "prior_close": f["prior_close"],
                            "prior_close_raw": f["prior_close_raw"], "close_before_prior": f["close_before_prior"],
                            "atr14": f["atr14"], "sigma_d": f["sigma_d"], "open": day["o"][0]}
                        traded = [x for x in day["c"] if x is not None]
                        closes[(ticker, session)] = (day["c"][59] if len(day["c"]) > 59 else None, traded[-1])
                hist.update(day, session)
    return {"months": list(months), "features": features, "closes": closes, "eligible": dict(eligible)}


# ---- the features (all known before the 09:31 entry) ----------------------------------------------------------------

def _adj(row: dict) -> float:
    return row.get("adj_close") or row["close"]


def features(trades: list[dict], days: dict, prices: dict, vols: dict) -> list[dict]:
    """One row per trade: the inputs, each flag and condition, S5 and each overlay's weight or keep. Inputs are the
    prior session's (its daily row, minute bars, IV and HV at its close) or the 09:30 open's (the gap)."""
    index = {t: {r["date"]: i for i, r in enumerate(rows)} for t, rows in prices.items()}
    spy_rows, spy_index = prices["SPY"], index["SPY"]
    iv, hv = vols["iv"], vols["hv"]
    spy_iv = iv.get("SPY", {})
    spy_iv_dates = sorted(spy_iv)
    out = []
    for t in trades:
        ticker, session, gap = t["ticker"], t["session"], t["gap"]
        f = days["features"].get((ticker, session))
        if f is None:
            raise ValueError(f"EXT_FADE_OVERLAYS_NOT_AN_ELIGIBLE_STOCK_DAY {ticker} {session}")
        if f["open"] / f["prior_close"] - 1 != gap:
            raise ValueError(f"EXT_FADE_OVERLAYS_GAP_DISAGREES_WITH_THE_LAB {ticker} {session}")
        prior = f["prior_date"]
        if not prior < session:
            raise ValueError(f"EXT_FADE_OVERLAYS_NOT_POINT_IN_TIME {ticker} {session}")
        sign = 1 if gap > 0 else -1
        prior_move = f["prior_close_raw"] / f["close_before_prior"] - 1
        i = index.get(ticker, {}).get(prior)
        row = prices[ticker][i] if i is not None else None
        range_atr = (row["high"] - row["low"]) / f["atr14"] if row is not None and f["atr14"] else None
        shape = days["closes"].get((ticker, prior))
        leg = (shape[1] / shape[0] - 1) * sign if shape is not None and shape[0] is not None else None
        iv_p, hv_p = iv.get(ticker, {}).get(prior), hv.get(ticker, {}).get(prior)
        vrp = iv_p / hv_p if iv_p and hv_p else None
        spy_now = spy_iv.get(prior)
        k = bisect.bisect_left(spy_iv_dates, prior) - 1               # the SPY IV observation before the prior close
        spy_before = spy_iv[spy_iv_dates[k]] if k >= 0 else None
        spy_change = spy_now / spy_before - 1 if spy_now is not None and spy_before else None
        j = spy_index.get(prior)
        spy_ret = _adj(spy_rows[j]) / _adj(spy_rows[j - 1]) - 1 if j is not None and j >= 1 else None
        rv = f["sigma_d"] * math.sqrt(252) if f["sigma_d"] is not None else None
        flags = {"prior_move": int(abs(prior_move) >= SW_FLAGS["prior_move"]),
                 "gap": int(abs(gap) >= SW_FLAGS["gap"]),
                 "prior_range_atr": int(range_atr is not None and range_atr >= SW_FLAGS["prior_range_atr"]),
                 "prior_leg": int(leg is not None and leg >= SW_FLAGS["prior_leg"]),
                 "iv": int(iv_p is not None and iv_p >= SW_FLAGS["iv"])}
        s5 = sum(flags.values())
        conditions = {"iv_over_hv": int(vrp is not None and vrp >= IVR_COND["iv_over_hv"]["at_least"]),
                      "spy_iv_change": int(spy_change is not None and spy_change > IVR_COND["spy_iv_change"]["above"]),
                      "iv": int(iv_p is not None and iv_p >= IVR_COND["iv"]["at_least"])}
        p2_met = int(abs(prior_move) >= P2_PRIOR)
        pxc = int(spy_ret is not None and spy_ret < PXC["spy_fell"]["below"]) + \
            int(rv is not None and rv >= PXC["rv"]["at_least"])
        out.append({
            "ticker": ticker, "session": session, "month": session[:7], "year": session[:4],
            "net_bps": t["net_bps"], "gross_bps": t["gross_bps"],
            "inputs": {"gap": gap, "prior_move": prior_move, "prior_range_atr": range_atr, "prior_leg": leg,
                       "iv": iv_p, "hv": hv_p, "iv_over_hv": vrp, "spy_iv_change": spy_change,
                       "spy_prior_return": spy_ret, "rv": rv, "prior_date": prior},
            "flags": flags, "s5": s5, "w_sw": s5 / S_BAR,
            "keep_p2": int(p2_met >= P2_MIN),
            "conditions": conditions, "ivr_met": sum(conditions.values()),
            "keep_ivr": int(sum(conditions.values()) >= IVR_MIN),
            "pxc": pxc, "iv_ge_hv": None if vrp is None else vrp >= IVR_COND["iv_over_hv"]["at_least"],
        })
    return out


# ---- statistics ----------------------------------------------------------------------------------------------------

def net(row: dict, cost: int) -> float:
    """Net bps per trade at `cost` bps a side: the lab's own figure where it reports that cost."""
    return row["net_bps"][cost] if cost in row["net_bps"] else row["gross_bps"] - 2 * cost


def _mean(values):
    values = [v for v in values if v is not None]
    return math.fsum(values) / len(values) if values else None


def diff_t(values: list[float], kept: list[bool], clusters: list[str]) -> float | None:
    """t of mean(kept) - mean(removed) with the leave-one-session-out jackknife: lab.session_t's variance,
    (G - 1) / G x the sum over sessions g of (the difference without g - the difference)^2."""
    by = defaultdict(lambda: [0.0, 0, 0.0, 0])
    for x, k, c in zip(values, kept, clusters):
        cell = by[c]
        if k:
            cell[0] += x
            cell[1] += 1
        else:
            cell[2] += x
            cell[3] += 1
    g = len(by)
    sk, nk = math.fsum(c[0] for c in by.values()), sum(c[1] for c in by.values())
    sr, nr = math.fsum(c[2] for c in by.values()), sum(c[3] for c in by.values())
    if g < 2 or not nk or not nr:
        return None
    d = sk / nk - sr / nr
    reps = []
    for c in by.values():
        if nk - c[1] == 0 or nr - c[3] == 0:
            return None
        reps.append((sk - c[0]) / (nk - c[1]) - (sr - c[2]) / (nr - c[3]))
    var = (g - 1) / g * math.fsum((r - d) ** 2 for r in reps)
    return d / math.sqrt(var) if var > 1e-24 else None


def book_sharpe(rows: list[dict], weights: list[float], cost: int, sessions: list[str]) -> float | None:
    """Daily book: each session's sum of weight x net (0 on a session without trades), mean / sd x sqrt(252)."""
    by = defaultdict(list)
    for r, w in zip(rows, weights):
        by[r["session"]].append(w * net(r, cost))
    pnl = [math.fsum(by[s]) for s in sessions]
    n = len(pnl)
    if n < 2:
        return None
    m = math.fsum(pnl) / n
    sd = math.sqrt(math.fsum((x - m) ** 2 for x in pnl) / (n - 1))
    return m / sd * math.sqrt(252) if sd > 0 else None


def _by_year(values: list[float], years_of: list[str], years) -> dict:
    return {y: _mean([v for v, yy in zip(values, years_of) if yy == y]) for y in years}


def _positive(by_year: dict) -> int:
    return sum(1 for v in by_year.values() if v is not None and v > 0)


def _t_ok(t: float | None, bar: dict) -> bool:
    if t is None:
        return False
    return t > bar["t_above"] if "t_above" in bar else t >= bar["t_at_least"]


def _mean_part(values, clusters, years_of, years, bar) -> dict:
    """A mean with its session t and its years, against a bar."""
    mean, t = _mean(values), lab.session_t(values, clusters)
    by_year = _by_year(values, years_of, years)
    passed = mean is not None and mean > bar["mean_above"] and _t_ok(t, bar) and \
        _positive(by_year) >= bar.get("min_positive_years", 0)
    return {"trades": len(values), "mean": mean, "t": t, "by_year": by_year, "positive_years": _positive(by_year),
            "pass": passed}


def _diff_part(rows, kept, cost, years, bar) -> dict:
    values = [net(r, cost) for r in rows]
    k = [x for x, keep in zip(values, kept) if keep]
    rm = [x for x, keep in zip(values, kept) if not keep]
    mean = _mean(k) - _mean(rm) if k and rm else None
    t = diff_t(values, kept, [r["session"] for r in rows])
    by_year = {}
    for y in years:
        ky = [x for x, keep, r in zip(values, kept, rows) if keep and r["year"] == y]
        ry = [x for x, keep, r in zip(values, kept, rows) if not keep and r["year"] == y]
        by_year[y] = _mean(ky) - _mean(ry) if ky and ry else None
    passed = mean is not None and mean > bar["mean_above"] and _t_ok(t, bar) and \
        _positive(by_year) >= bar.get("min_positive_years", 0)
    return {"kept": len(k), "removed": len(rm), "kept_mean": _mean(k), "removed_mean": _mean(rm), "mean": mean,
            "t": t, "by_year": by_year, "positive_years": _positive(by_year), "pass": passed}


def _lab_check_bar(rows: list[dict], years) -> dict:
    """The lab's check bar on a set of trades. On 2020..2023 it is lab.pass_bar(trades, 'check') itself; on other
    years (the in-sample dry run) the same rule with those years as its periods."""
    trades = [{"session": r["session"], "month": r["month"], "net_bps": r["net_bps"]} for r in rows]
    if tuple(years) == YEARS:
        pb = lab.pass_bar(trades, "check")
        return {"mean": pb["mean_net_bps"], "t": pb["session_t"], "positive_periods": pb["positive_periods"],
                "pass": pb["verdict"] == "PASS"}
    bar = lab.PASS_BARS["check"]
    values = [net(r, bar["cost_bps"]) for r in rows]
    part = _mean_part(values, [r["session"] for r in rows], [r["year"] for r in rows], years,
                      {"mean_above": 0.0, "t_above": bar["min_t"], "min_positive_years": bar["min_positive_periods"]})
    return {"mean": part["mean"], "t": part["t"], "positive_periods": part["positive_years"], "pass": part["pass"]}


def sw_weights(rows: list[dict]) -> tuple[list[float], float | None]:
    """The SW weights normalised by the period's own mean weight: equal exposure with v1."""
    w = [r["w_sw"] for r in rows]
    w_bar = math.fsum(w) / len(w) if w else None
    return ([x / w_bar for x in w] if w_bar else [0.0] * len(w)), w_bar


def evaluate(rows: list[dict], sessions: list[str], years=YEARS) -> dict:
    """Each overlay against its pass bar, on the feature rows of one period's v1 trades. sessions: the period's
    sessions with an eligible stock-day (the book's days). years: the years the bars count (2020..2023 at the
    check; the in-sample dry run passes its own)."""
    years = tuple(years)
    clusters = [r["session"] for r in rows]
    years_of = [r["year"] for r in rows]
    ones = [1.0] * len(rows)
    out = {"years": list(years), "trades": len(rows), "sessions": len(sessions)}
    # EXT-FADE.v1 itself, for reference
    out[BASE] = {"trades": len(rows), **{f"mean_at_{c}": _mean([net(r, c) for r in rows]) for c in (0, 2, 5, 10, 15)},
                 "t_at_5": lab.session_t([net(r, 5) for r in rows], clusters),
                 "t_at_10": lab.session_t([net(r, 10) for r in rows], clusters),
                 "sharpe_at_5": book_sharpe(rows, ones, 5, sessions),
                 "sharpe_at_10": book_sharpe(rows, ones, 10, sessions)}
    # SW
    bars = PASS_BARS[SW]
    norm, w_bar = sw_weights(rows)
    c = bars["standalone"]["cost_bps"]
    v = [w * net(r, c) for w, r in zip(norm, rows)]
    d = [(w - 1) * net(r, c) for w, r in zip(norm, rows)]
    standalone = _mean_part(v, clusters, years_of, years, bars["standalone"])
    gain = _mean_part(d, clusters, years_of, years, bars["gain"])
    sharpe = _sharpe_part(rows, norm, bars["book_sharpe"]["cost_bps"], sessions)
    out[SW] = {"w_bar": w_bar, "s5_mean": _mean([r["s5"] for r in rows]),
               "s5_counts": {s: sum(1 for r in rows if r["s5"] == s) for s in range(6)},
               "no_trade": sum(1 for r in rows if r["s5"] == 0), "standalone": standalone, "gain": gain,
               "book_sharpe": sharpe, "unit_mean_at_10": _mean([w * net(r, 10) for w, r in zip(norm, rows)])}
    # P2 and IVR
    for sid, key in ((P2, "keep_p2"), (IVR, "keep_ivr")):
        bars = PASS_BARS[sid]
        kept = [bool(r[key]) for r in rows]
        k_rows = [r for r, k in zip(rows, kept) if k]
        c = bars["kept"]["cost_bps"]
        part = _mean_part([net(r, c) for r in k_rows], [r["session"] for r in k_rows], [r["year"] for r in k_rows],
                          years, bars["kept"])
        if "and_lab_check_bar" in bars["kept"]:
            part["lab_check_bar"] = _lab_check_bar(k_rows, years)
            part["pass"] = part["pass"] and part["lab_check_bar"]["pass"]
        kmr = _diff_part(rows, kept, bars["kept_minus_removed"]["cost_bps"], years, bars["kept_minus_removed"])
        sharpe = _sharpe_part(rows, [1.0 if k else 0.0 for k in kept], bars["book_sharpe"]["cost_bps"], sessions)
        out[sid] = {"kept": part, "kept_minus_removed": kmr, "book_sharpe": sharpe, "kept_trades": sum(kept),
                    "stocks": len({r["ticker"] for r in k_rows})}
    out[SW]["verdict"] = _verdict(out[SW], ("standalone", "gain", "book_sharpe"))
    out[P2]["verdict"] = _verdict(out[P2], ("kept", "kept_minus_removed", "book_sharpe"))
    out[IVR]["verdict"] = _verdict(out[IVR], ("kept", "kept_minus_removed", "book_sharpe"))
    out[IVR]["secondary"] = secondary(rows, years)
    for sid in (SW, P2, IVR):
        out[sid]["standalone_pass"] = out[sid][STANDALONE[sid]]["pass"]
    return out


def _sharpe_part(rows: list[dict], weights: list[float], cost: int, sessions: list[str]) -> dict:
    """The overlay's daily book Sharpe against v1's at the same cost: at least v1's passes."""
    mine, v1 = book_sharpe(rows, weights, cost, sessions), book_sharpe(rows, [1.0] * len(rows), cost, sessions)
    return {"cost_bps": cost, "overlay": mine, BASE: v1, "pass": mine is not None and v1 is not None and mine >= v1}


def _verdict(result: dict, parts) -> str:
    return "PASS" if all(result[p]["pass"] for p in parts) else "FAIL"


def secondary(rows: list[dict], years=YEARS) -> dict:
    """Among trades with PXC at most 1, IV >= HV minus IV < HV at 5 bps (reported, not gating)."""
    bar = PASS_BARS[IVR]["secondary"]
    sub = [r for r in rows if r["pxc"] <= PXC_MAX and r["iv_ge_hv"] is not None]
    part = _diff_part(sub, [bool(r["iv_ge_hv"]) for r in sub], bar["cost_bps"], years, bar)
    part["among"] = len(sub)
    part["verdict"] = "PASS" if part["pass"] else "FAIL"
    part["reading"] = ("IV >= HV adds to the price-only stand-ins" if part["pass"] else
                       "the options data adds nothing over prices here")
    return part


# ---- stress tables -------------------------------------------------------------------------------------------------

def _book_weights(rows: list[dict], book: str) -> list[float]:
    if book == BASE:
        return [1.0] * len(rows)
    if book == SW:
        return [r["w_sw"] for r in rows]
    return [float(r["keep_p2" if book == P2 else "keep_ivr"]) for r in rows]


def _unit(rows: list[dict], weights: list[float], cost: int, *, sizing: bool) -> dict:
    """Mean net per unit of position. A sizing book: v_i = (w_i / w_bar) x net_i over all its trades (w_bar the
    subset's own mean weight); a filter: the kept trades' net. With the session t."""
    if sizing:
        w_bar = _mean(weights)
        pairs = [(w / w_bar if w_bar else 0.0, r) for w, r in zip(weights, rows)]
    else:
        pairs = [(1.0, r) for w, r in zip(weights, rows) if w > 0]
    values = [w * net(r, cost) for w, r in pairs]
    return {"trades": sum(1 for w, r in pairs if w > 0), "mean": _mean(values),
            "t": lab.session_t(values, [r["session"] for _, r in pairs]) if values else None}


def _drop(rows, weights, cost, *, sessions_best=0, sessions_worst=0, stocks=0):
    """Drop the book's best and worst sessions, or its top stocks, by its own P&L at `cost` (ties by name)."""
    by_s, by_t = defaultdict(list), defaultdict(list)
    for r, w in zip(rows, weights):
        by_s[r["session"]].append(w * net(r, cost))
        by_t[r["ticker"]].append(w * net(r, cost))
    s_pnl = sorted(((math.fsum(v), s) for s, v in by_s.items()), key=lambda x: (-x[0], x[1]))
    drop_s = {s for _, s in s_pnl[:sessions_best]}
    if sessions_worst:
        drop_s |= {s for _, s in s_pnl[-sessions_worst:]}
    t_pnl = sorted(((math.fsum(v), t) for t, v in by_t.items()), key=lambda x: (-x[0], x[1]))
    drop_t = {t for _, t in t_pnl[:stocks]}
    keep = [(r, w) for r, w in zip(rows, weights) if r["session"] not in drop_s and r["ticker"] not in drop_t]
    return [r for r, _ in keep], [w for _, w in keep], sorted(drop_t)


def stress(rows: list[dict], rows_0932: list[dict], years=YEARS) -> dict:
    """For v1 and each overlay: costs of 10 and 15 bps, entry at 09:32, without the best 20 sessions, without the top 3
    stocks, the best and worst 20 sessions trimmed, and each year. Mean net per unit of position, with the session t."""
    c = STRESS["cost_bps"]
    out = {}
    for book in BOOKS:
        sizing = book == SW
        w = _book_weights(rows, book)
        table = {f"at_{c}": _unit(rows, w, c, sizing=sizing)}
        for cost in STRESS["costs_bps"]:
            table[f"at_{cost}"] = _unit(rows, w, cost, sizing=sizing)
        w32 = _book_weights(rows_0932, book)
        table[f"entry_0932_at_{c}"] = _unit(rows_0932, w32, c, sizing=sizing)
        table["entry_0932_at_10"] = _unit(rows_0932, w32, 10, sizing=sizing)
        r2, w2, _ = _drop(rows, w, c, sessions_best=STRESS["drop_best_sessions"])
        table[f"without_best_{STRESS['drop_best_sessions']}_sessions"] = _unit(r2, w2, c, sizing=sizing)
        r2, w2, top = _drop(rows, w, c, stocks=STRESS["drop_top_stocks"])
        table[f"without_top_{STRESS['drop_top_stocks']}_stocks"] = {**_unit(r2, w2, c, sizing=sizing), "stocks": top}
        n = STRESS["trim_sessions"]
        r2, w2, _ = _drop(rows, w, c, sessions_best=n, sessions_worst=n)
        table[f"best_and_worst_{n}_sessions_trimmed"] = _unit(r2, w2, c, sizing=sizing)
        table["by_year"] = {}
        for y in years:
            ry = [(r, x) for r, x in zip(rows, w) if r["year"] == y]
            table["by_year"][y] = {f"at_{k}": _unit([r for r, _ in ry], [x for _, x in ry], k, sizing=sizing)
                                   for k in (c, 10)}
        out[book] = table
    return out


# ---- the one check -------------------------------------------------------------------------------------------------

def read_log(path=None) -> list[dict]:
    path = Path(path or LOG_PATH)
    return json.loads(path.read_text()) if path.exists() else []


def write_log(entries: list[dict], path=None) -> None:
    path = Path(path or LOG_PATH)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as fh:
        fh.write(json.dumps(entries, indent=1, default=str) + "\n")
    os.replace(fh.name, path)


def v1_check_entry(lab_log: list[dict]) -> tuple[dict | None, str]:
    """EXT-FADE.v1's one-time check in the lab's log (keyed on its spec, as the lab keys it)."""
    strategy = lab.spec_sha(BASE_SPEC)
    found = [e for e in lab_log if e.get("split") == "check" and lab._same_strategy(e, strategy)]
    if not found:
        return None, "NO_V1_CHECK_IN_THE_LAB_LOG"
    if len(found) > 1:
        return None, "V1_CHECKED_MORE_THAN_ONCE"
    return found[0], "OK"


def preflight(registration: dict | None, lab_log: list[dict], log: list[dict], runner_path) -> tuple[dict | None, str]:
    """Everything that must hold before a check bar is read. Returns (v1's check entry, "OK") or (None, the reason)."""
    if registration is None:
        return None, "NOT_REGISTERED"
    if registration.get("module_sha256") != module_sha():
        return None, "MODULE_NOT_REGISTERED"
    if registration.get("runner_sha256") != file_sha(runner_path):
        return None, "RUNNER_NOT_REGISTERED"
    if {k: v.get("spec_sha256") for k, v in registration.get("specs", {}).items()} != \
            {sid: spec_sha(sid) for sid in SPECS}:
        return None, "SPECS_NOT_REGISTERED"
    if lab.lab_code_sha() != lab.CODE_SHA:
        return None, "LAB_CODE_CHANGED_SINCE_IMPORT"
    if registration.get("lab_code_sha256") != lab.CODE_SHA:
        return None, "LAB_CODE_NOT_REGISTERED"
    entry, why = v1_check_entry(lab_log)
    if entry is None:
        return None, why
    if entry.get("lab_code_sha256") != lab.CODE_SHA:
        return None, "LAB_CODE_IS_NOT_V1_CHECK_CODE"
    if list(entry.get("months", [])) != list(CHECK):
        return None, "V1_CHECK_MONTHS_DIFFER"
    keys = {spec_sha(sid) for sid in SPECS}
    if any(e.get("split") == "check" and e.get("spec_sha256") in keys for e in log):
        return None, "ALREADY_CHECKED"
    return entry, "OK"


def v1_reproduced(trades: list[dict], entry: dict) -> bool:
    """The check's v1 trades are exactly those the lab logged: the same count and the same mean net at 10 bps."""
    mean = lab._mean([t["net_bps"][lab.HEADLINE_COST_BPS] for t in trades])
    return len(trades) == entry["trades"] and mean == entry["net_bps_at_10"]


def run_check(*, dev_root, prices_dir, iv_dir, tickers: list[str], registration_path=None, lab_log_path=None,
              log_path=None, runner_path=None) -> dict:
    """The one check of the three overlays on 2020-01..2023-12. Refused unless everything registered holds; the log
    entries are written under a lock before this returns."""
    log_path = Path(log_path or LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path.with_name(log_path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)             # one run at a time reads, checks and appends the log
        return _run_locked(dev_root=dev_root, prices_dir=prices_dir, iv_dir=iv_dir, tickers=sorted(tickers),
                           registration_path=Path(registration_path or REGISTRATION_PATH),
                           lab_log_path=Path(lab_log_path or lab.LOG_PATH), log_path=log_path,
                           runner_path=Path(runner_path or RUNNER_PATH))


def _refused(reason: str, **extra) -> dict:
    return {"status": "REFUSED", "overlay_id": OVERLAY_ID, "reason": reason, **extra}


def _run_locked(*, dev_root, prices_dir, iv_dir, tickers, registration_path, lab_log_path, log_path, runner_path):
    registration = json.loads(registration_path.read_text()) if registration_path.exists() else None
    log = read_log(log_path)
    entry, why = preflight(registration, lab.read_log(lab_log_path), log, runner_path)
    if entry is None:
        return _refused(why)
    if lab.inputs_sha(tickers) != entry.get("inputs_sha256"):
        return _refused("INPUTS_ARE_NOT_V1_CHECK_INPUTS")
    symbols = tickers + ["SPY"]
    if ir.missing_months(dev_root, symbols, [ir.previous_month(CHECK[0])] + ir.months_between(*CHECK)):
        return _refused("CHECK_FETCH_INCOMPLETE")
    vols, vol_sha = load_vols(iv_dir, CHECK_LAST_DAY)
    if vol_sha != registration["iv_hv"]["sha256"]:
        return _refused("IV_HV_NOT_REGISTERED")
    prices, prices_sha = ivr.load_prices(prices_dir)
    if prices_sha != ir.DAILY_PRICES_SHA256:
        return _refused("DAILY_PRICES_NOT_REGISTERED")
    if ir.behavior_digest() != ir.BEHAVIOR_SHA256:
        return _refused("REPLAY_CODE_CHANGED")
    bars_sha = ir.files_sha(dev_root, ir.detection_months(CHECK))
    if bars_sha != entry.get("bars_sha256"):
        return _refused("BARS_ARE_NOT_V1_CHECK_BARS")

    def bars_for(ticker, month):
        if month > CHECK[1]:
            raise ValueError("EXT_FADE_OVERLAYS_NEVER_READ_THE_HOLDOUT")
        return ir.load_month(dev_root, ticker, month)
    # v1's check replay, with the lab's own inputs (the same call lab.run makes), unlogged; plus the 09:32 entry
    run = lab.replay(bars_for, prices, CHECK, tickers, {BASE: BASE_SPEC, ENTRY_0932: STRESS["entry_0932"]},
                     split="check")
    v1 = [t for t in run["trades"] if t["variant"] == BASE]
    if not v1_reproduced(v1, entry):
        return _refused("V1_CHECK_NOT_REPRODUCED", trades=len(v1), logged_trades=entry["trades"])
    days = stock_days(bars_for, prices, CHECK, tickers)
    if days["eligible"] != run["eligible"]:
        return _refused("ELIGIBILITY_DISAGREES_WITH_THE_LAB")
    daily = cut_prices(prices, CHECK_LAST_DAY)        # the overlays read nothing after the check's last day
    rows = features(v1, days, daily, vols)
    rows_0932 = features([t for t in run["trades"] if t["variant"] == ENTRY_0932], days, daily, vols)
    sessions = sorted(run["eligible"])
    result = evaluate(rows, sessions, YEARS)
    tables = stress(rows, rows_0932, YEARS)
    entries = log_entries(result, log, entry=entry, registration=registration, bars_sha=bars_sha,
                          prices_sha=prices_sha, vol_sha=vol_sha, tickers=tickers)
    write_log(log + entries, log_path)
    return {"status": "OK", "overlay_id": OVERLAY_ID, "split": "check", "months": list(CHECK),
            "generated_at": entries[0]["at"], "v1_check": {k: entry.get(k) for k in
                                                           ("entry", "trades", "net_bps_at_10", "verdict")},
            "result": result, "stress": tables, "log_entries": entries,
            "inputs": {"bars_sha256": bars_sha, "daily_prices_sha256": prices_sha, "iv_hv_sha256": vol_sha,
                       "lab_code_sha256": lab.CODE_SHA, "module_sha256": module_sha(),
                       "runner_sha256": file_sha(runner_path), "inputs_sha256": lab.inputs_sha(tickers)},
            "sessions": len(sessions), "verdicts": VERDICTS, "use": "INFORMATION_ONLY"}


def log_entries(result: dict, log: list[dict], *, entry: dict, registration: dict, bars_sha: str, prices_sha: str,
                vol_sha: str, tickers: list[str]) -> list[dict]:
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    v1_passed = entry.get("verdict") == "PASS"
    out = []
    for i, sid in enumerate((SW, P2, IVR)):
        r = result[sid]
        counts = r["verdict"] == "PASS" and v1_passed
        out.append({
            "entry": len(log) + i + 1, "at": at, "overlay_id": OVERLAY_ID, "split": "check", "months": list(CHECK),
            "spec_id": sid, "spec": SPECS[sid], "spec_sha256": spec_sha(sid), "module_sha256": module_sha(),
            "runner_sha256": registration["runner_sha256"], "lab_code_sha256": lab.CODE_SHA,
            "v1_check_entry": entry.get("entry"), "v1_check_verdict": entry.get("verdict"),
            "iv_hv_sha256": vol_sha, "daily_prices_sha256": prices_sha, "bars_sha256": bars_sha,
            "inputs_sha256": lab.inputs_sha(tickers), "trades": result["trades"],
            "result": r, "verdict": r["verdict"],
            "reading": ("counts as an overlay on v1" if counts else
                        "v1 failed its check: this overlay can go forward only as a new strategy" if
                        not v1_passed and r["standalone_pass"] else "does not go forward"),
        })
    return out


# ---- the summary ---------------------------------------------------------------------------------------------------

def _f(x, fmt="+.1f") -> str:
    return "–" if x is None else format(x, fmt)


def _yes(x: bool) -> str:
    return "yes" if x else "no"


def render(report: dict) -> str:
    """The check's summary in plain terms."""
    r = report["result"]
    v1 = r[BASE]
    lines = [f"# EXT-FADE.v1 overlays: the one check, {report['months'][0]}..{report['months'][1]}", "",
             f"`{OVERLAY_ID}`, generated {report['generated_at']}. The three overlays frozen on 2026-10-24, run once "
             f"on EXT-FADE.v1's check trades (lab log entry {report['v1_check']['entry']}, verdict "
             f"{report['v1_check']['verdict']}). Each verdict against the pass bar fixed beforehand is final. Log: "
             "`docs/evidence/ext-fade-overlays-log.json`.", "",
             f"- **EXT-FADE.v1:** {v1['trades']:,} trades; net bps per trade {_f(v1['mean_at_5'])} at 5 bps a side "
             f"(t {_f(v1['t_at_5'], '.2f')}), {_f(v1['mean_at_10'])} at 10; book Sharpe {_f(v1['sharpe_at_5'], '.2f')} "
             f"at 5, {_f(v1['sharpe_at_10'], '.2f')} at 10. {r['sessions']:,} sessions.", ""]
    sw = r[SW]
    lines += [f"## {SW} (sizing by S5 / {S_BAR}): {sw['verdict']}", "",
              f"Mean S5 {_f(sw['s5_mean'], '.3f')}; no trade (S5 = 0) on {sw['no_trade']} of {r['trades']}.", "",
              "| Part | Mean | t | " + " | ".join(r["years"]) + " | Pass |",
              "|---|---|---|" + "---|" * len(r["years"]) + "---|"]
    for part, label in (("standalone", "per unit of position"), ("gain", "gain over v1 at equal exposure")):
        p = sw[part]
        lines.append(f"| {label}, {PASS_BARS[SW][part]['cost_bps']} bps | {_f(p['mean'])} | {_f(p['t'], '.2f')} | "
                     + " | ".join(_f(p["by_year"][y]) for y in r["years"]) + f" | {_yes(p['pass'])} |")
    s = sw["book_sharpe"]
    lines += ["", f"Book Sharpe at {s['cost_bps']} bps: {_f(s['overlay'], '.2f')} vs v1 {_f(s[BASE], '.2f')} "
              f"(pass: {_yes(s['pass'])}).", ""]
    for sid in (P2, IVR):
        x = r[sid]
        k, d, s = x["kept"], x["kept_minus_removed"], x["book_sharpe"]
        lines += [f"## {sid} (filter): {x['verdict']}", "",
                  f"Kept {x['kept_trades']} of {r['trades']} trades.", "",
                  "| Part | Mean | t | " + " | ".join(r["years"]) + " | Pass |",
                  "|---|---|---|" + "---|" * len(r["years"]) + "---|",
                  f"| kept, {PASS_BARS[sid]['kept']['cost_bps']} bps | {_f(k['mean'])} | {_f(k['t'], '.2f')} | "
                  + " | ".join(_f(k["by_year"][y]) for y in r["years"]) + f" | {_yes(k['pass'])} |",
                  f"| kept minus removed, {PASS_BARS[sid]['kept_minus_removed']['cost_bps']} bps | {_f(d['mean'])} | "
                  f"{_f(d['t'], '.2f')} | " + " | ".join(_f(d["by_year"][y]) for y in r["years"])
                  + f" | {_yes(d['pass'])} |",
                  "", f"Book Sharpe at {s['cost_bps']} bps: {_f(s['overlay'], '.2f')} vs v1 {_f(s[BASE], '.2f')} "
                  f"(pass: {_yes(s['pass'])})."]
        if "lab_check_bar" in k:
            lb = k["lab_check_bar"]
            lines.append(f"The lab's check bar on the kept trades: mean {_f(lb['mean'])} at 5 bps, "
                         f"t {_f(lb['t'], '.2f')}, positive in {lb['positive_periods']} of {len(r['years'])} years "
                         f"(pass: {_yes(lb['pass'])}).")
        lines.append("")
    sec = r[IVR]["secondary"]
    lines += ["## The secondary question (not gating)", "",
              f"Among {sec['among']} trades with PXC at most {PXC_MAX}: IV >= HV {_f(sec['kept_mean'])} "
              f"({sec['kept']}) vs IV < HV {_f(sec['removed_mean'])} ({sec['removed']}) at 5 bps; difference "
              f"{_f(sec['mean'])}, t {_f(sec['t'], '.2f')}: {sec['verdict']}, {sec['reading']}.", "",
              "## Stress (not gating): mean net per unit of position, bps", ""]
    names = list(next(iter(report["stress"].values())))
    names = [n for n in names if n != "by_year"]
    lines += ["| Book | " + " | ".join(n.replace("_", " ") for n in names) + " |", "|---|" + "---|" * len(names)]
    for book, table in report["stress"].items():
        lines.append(f"| {book} | " + " | ".join(f"{_f(table[n]['mean'])} (t {_f(table[n]['t'], '.2f')})"
                                                  for n in names) + " |")
    lines += ["", "| Book | " + " | ".join(f"{y} at 5 | {y} at 10" for y in r["years"]) + " |",
              "|---|" + "---|" * (2 * len(r["years"]))]
    for book, table in report["stress"].items():
        years = table["by_year"]
        lines.append(f"| {book} | " + " | ".join(f"{_f(years[y]['at_5']['mean'])} | {_f(years[y]['at_10']['mean'])}"
                                                  for y in r["years"]) + " |")
    lines += ["", report["verdicts"]]
    return "\n".join(lines) + "\n"
