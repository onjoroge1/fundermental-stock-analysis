"""EXT-FADE-CB.v1: EXT-FADE.v1's own trades on a constrained book, frozen 2026-10-24 and checked once.

EXT-FADE.v1 (intraday_lab.py) fades a gap of at least 1% that extends yesterday's move of at least 1%, from 09:31 to
the gap fill or the close, one equal notional per trade, each trade hedged by beta x SPY over its own minutes. It
passed its confirm on 2017-07..2019-12. This spec (tracker item 77, owner-approved candidate 2) changes two things
only, on exactly v1's check trades:

  (a) position size: every 09:31 candidate starts at one unit and is scaled down in proportion, once per session at
      09:31 from that session's candidates alone, so that at most K = 4 units sit on one side of one GICS industry
      group (a cluster), at most S = 16/3 in one group and at most G = 16 in the book;
  (b) the SPY hedge: the book holds one aggregate SPY position; each trade's beta x SPY hedge is added at its entry
      and removed at its exit, and the SPY the book trades at each event, netted, costs 0.5 bps a side.

No trade is ever added or dropped. Signals, entries, exits, Rule 201, the ENTRY_BEYOND_TARGET skip and each trade's
residual are v1's, untouched. The pass bar (PASS_BAR) compares the daily P&L with v1's equal-notional book at equal
average gross: the tail (expected shortfall at 5%) must shrink with a one-sided 95% bound below 1, three other
downside measures must improve, and the Sharpe ratio may not fall by more than 0.30 at a one-sided 95% bound.

The one check (scripts/run_ext_fade_portfolio.py) runs on 2020-01..2023-12 after EXT-FADE.v1's own one-time check
(in the lab's log) and the three overlays' one-time check (in ext-fade-overlays-log.json). It refuses unless the
module (on disk and as imported), the runner, the spec, its parameters and its sector map are the registered ones
(docs/evidence/ext-fade-portfolio-registration-2026-10-25.json), the lab's code is the code of v1's check, v1's
check trades reproduce exactly and match this module's own pass over the stock-days trade by trade, and its own log
holds no check at all; the log is written under a lock before it returns.

What the check reads: no bar month after 2023-12 is opened. The daily price rows go uncut to lab.replay and to this
module's stock-day pass, exactly as lab.run passed them for v1's check, because eligibility must be judged as the
lab judges it; the lab's printing-error test reads the next session's open, so the last 2023 session's eligibility
reads the 2024-01-02 open. Nothing else here reads a daily row, and no IV or HV data is read.

The lab, the replay and the overlays' module are imported read-only. Information only: nothing here trades.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..agents.contracts import AGENT_UNIVERSE, digest
from . import ext_fade_overlays as ov
from . import implied_vol_replay as ivr
from . import intraday_lab as lab
from . import intraday_replay as ir

BOOK_ID = "ext-fade-portfolio.v1"
SPEC_ID = "EXT-FADE-CB.v1"
BASE = ov.BASE                          # "EXT-FADE.v1"
BASE_SPEC = ov.BASE_SPEC
CALIBRATION = ("2016-01", "2019-12")    # every month before the check
CALIBRATION_LAST_DAY = "2019-12-31"
CALIBRATION_YEARS = ("2016", "2017", "2018", "2019")
HALVES = {"H1": ("2016-01", "2017-12"), "H2": ("2018-01", "2019-12")}
CHECK = lab.CHECK                       # 2020-01..2023-12
CHECK_LAST_DAY = "2023-12-31"
YEARS = tuple(f"{y}" for y in range(2020, 2024))
HOLDOUT_FROM = lab.HOLDOUT_FROM
ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / "docs" / "evidence" / "ext-fade-portfolio-log.json"
REGISTRATION_PATH = ROOT / "docs" / "evidence" / "ext-fade-portfolio-registration-2026-10-25.json"
RUNNER_PATH = ROOT / "scripts" / "run_ext_fade_portfolio.py"
OVERLAYS_MODULE = "stock_machine/backtest/ext_fade_overlays.py"
OVERLAYS_RUNNER = "scripts/run_ext_fade_overlays.py"
OVERLAYS_REGISTRATION = "docs/evidence/ext-fade-overlays-registration-2026-10-24.json"
PROTECTED = ov.PROTECTED + (OVERLAYS_MODULE, OVERLAYS_RUNNER)
LATE = ("DAILY_ROW_ERROR", "DAILY_CLOSE_MISMATCH")     # exclusions that read data from after 09:30
TRADE = "TRADE"
POINTS = 391                            # the intraday trough's points: the close of each minute 09:30..15:59, then
                                        # the official close

# ---- the frozen sector map: 13 GICS industry groups --------------------------------------------------------------
# Each ticker's GICS industry group under the structure in force from 2018-09-28 to 2023-03-17, as of 2019-12-31
# (names listed later: their first assignment under that structure). The 2023-03-17 change is not applied.

SECTOR_MAP = {
    "ADI": "SEMI", "AMAT": "SEMI", "AMD": "SEMI", "AVGO": "SEMI", "INTC": "SEMI", "KLAC": "SEMI", "LRCX": "SEMI",
    "MU": "SEMI", "NVDA": "SEMI", "QCOM": "SEMI", "TXN": "SEMI",
    "ADBE": "SOFT", "CRM": "SOFT", "IBM": "SOFT", "INTU": "SOFT", "MSFT": "SOFT", "NOW": "SOFT", "ORCL": "SOFT",
    "PLTR": "SOFT",
    "AAPL": "HARD", "CSCO": "HARD", "DELL": "HARD", "HPQ": "HARD",
    "CMCSA": "MEDIA", "DIS": "MEDIA", "GOOGL": "MEDIA", "META": "MEDIA", "NFLX": "MEDIA",
    "T": "TELCO", "TMUS": "TELCO", "VZ": "TELCO",
    "ABNB": "RETAIL", "AMZN": "RETAIL", "BKNG": "RETAIL", "HD": "RETAIL", "LOW": "RETAIL", "TGT": "RETAIL",
    "COST": "STAPLES", "WMT": "STAPLES",
    "CMG": "CONSVC", "MCD": "CONSVC", "SBUX": "CONSVC",
    "NKE": "DURABLES",
    "F": "AUTOS", "GM": "AUTOS", "RIVN": "AUTOS", "TSLA": "AUTOS",
    "AAL": "TRANSPORT", "DAL": "TRANSPORT", "LUV": "TRANSPORT", "UAL": "TRANSPORT", "UBER": "TRANSPORT",
    "SOFI": "DIVFIN",
    "HIMS": "HCES",
}
GROUP_NAMES = {
    "SEMI": "Semiconductors & Semiconductor Equipment", "SOFT": "Software & Services",
    "HARD": "Technology Hardware & Equipment", "MEDIA": "Media & Entertainment",
    "TELCO": "Telecommunication Services", "RETAIL": "Retailing", "STAPLES": "Food & Staples Retailing",
    "CONSVC": "Consumer Services", "DURABLES": "Consumer Durables & Apparel", "AUTOS": "Automobiles & Components",
    "TRANSPORT": "Transportation", "DIVFIN": "Diversified Financials",
    "HCES": "Health Care Equipment & Services",
}
assert set(SECTOR_MAP) == set(AGENT_UNIVERSE) and len(SECTOR_MAP) == 54, "the map must cover the universe exactly"
assert set(SECTOR_MAP.values()) == set(GROUP_NAMES) and len(GROUP_NAMES) == 13

# ---- the frozen constants and the pass bar --------------------------------------------------------------------------

CONSTANTS = {
    "base_notional": 1,                 # v1's equal per-trade notional
    "K": 4,                             # the most candidate gross on one (group, side) cluster
    "S": 16 / 3,                        # the most in one group (G / 3)
    "G": 16,                            # the most in the book
    "K_MAX": 2.5,                       # the largest normalised position, k x s_i <= k: the capacity band
    "nesting": ["cluster", "group", "book"],
    "cost_bps": 5,                      # stock, per side, on x_i at entry and at exit
    "spy_cost_bps": 0.5,                # SPY, per side, on the netted SPY traded at each event
    "es_level": 0.05,
    "w10": 10,
    "block": 21,                        # one trading month
    "reps": 10_000,
    "seed": 20261024,                   # the freeze date
    "ni_margin": 0.30,
    "k_cal": 1.086957,                  # 1.888668 / 1.737575, from 2016-19 exposure: reported, not gating
}
PASS_BAR = {
    "months": list(CHECK),
    "cost_bps": 5, "spy_cost_bps": 0.5,
    "normalisation": "EQUAL_AVERAGE_ENTRY_GROSS",
    "G1": {"k_at_most": 2.5},
    "G2": {"r_es_below": 1.0, "ub95_below": 1.0, "ub95_percentile": 95},
    "G3": {"w10_above_v1": True, "dd_below_v1": True, "mdd_above_v1": True},
    "G4": {"lb95_dsr_above": -0.30, "lb95_percentile": 5},
    "pass": ["G1", "G2", "G3", "G4"],
    "counts_only_if_v1_check": "PASS",
}
assert PASS_BAR["G1"]["k_at_most"] == CONSTANTS["K_MAX"] and PASS_BAR["G4"]["lb95_dsr_above"] == -CONSTANTS["ni_margin"]
assert (PASS_BAR["cost_bps"], PASS_BAR["spy_cost_bps"]) == (CONSTANTS["cost_bps"], CONSTANTS["spy_cost_bps"])
STRESS = {
    "stock_costs_bps": [10, 15], "spy_costs_bps": [0, 1],
    "normalisations": ["K_CAL", "EQUAL_VOL", "TIME_WEIGHTED", "UNNORMALISED"],
    "post_skip_candidates": True, "late_excluded": True, "leave_one_year_out": True, "by_year": True,
    "intraday_trough": True, "gross": ["peak", "p99_of_active_sessions"], "binding": True,
    "concentration": {"best_days": 10, "top_stocks": 3}, "correlation": True,
}

# ---- the frozen spec, its wording included ---------------------------------------------------------------------------

SPEC = {
    "id": SPEC_ID,
    "what": ("EXT-FADE.v1 with a constrained book (owner-approved candidate 2, tracker item 77). One frozen spec, not "
             "a family. It changes only (a) position size, through book limits applied by proportional scale-down, "
             "and (b) how the SPY hedge is held and charged. No trade is ever added or dropped. Information only: "
             "nothing trades."),
    "history": ("Designed 2026-10-24, and reviewed and corrected on 2026-10-24 by an independent skeptic. Neither the "
                "design nor the review read anything dated 2020-01-01 or later. Every cap, cost and evaluation "
                "constant was fixed before the single in-sample preview (2016-19), and none was changed after it or "
                "after the review. The review changed only build mechanics (data handling, reconciliations, RNG "
                "pinning, runner checks), added the end-of-day exclusions to the candidate set (no effect in "
                "2016-19), separated the hold-out family, and corrected the disclosures."),
    "scope": {
        "on": {"variant": BASE, "spec": BASE_SPEC},
        "trades": ("EXT-FADE.v1's own check trades, unweighted: v1 = [t for t in run['trades'] if t['variant'] == "
                   "'EXT-FADE.v1'], with run = lab.replay(bars_for, prices, lab.CHECK, tickers, {'EXT-FADE.v1': "
                   "lab.VARIANTS['EXT-FADE.v1']}, split='check'). Not the SW weights or the P2/IVR subsets."),
        "unchanged": ("signals, entry, exit, Rule 201, the ENTRY_BEYOND_TARGET skip and the per-trade residual are "
                      "v1's, untouched"),
        "changes": ("only (a) position size, through book limits applied by proportional scale-down, and (b) how the "
                    "SPY hedge is held and charged; no trade is ever added or dropped"),
        "counts_only_if": "v1's check verdict is PASS",
    },
    "per_trade": {
        "g": ("gross_bps from the lab's record: the hedged residual, side x return - side x beta x SPY over the "
              "trade's own minutes, in bps"),
        "side": "-1 if gap > 0, else +1",
        "beta": ("f['beta'] from ir.daily_features in this module's own stock-day pass: the beta lab.score uses, "
                 "trailing and known before the open"),
        "exit_event": ("TARGET: bar entry_bar + minutes (the lab's minutes = exit_bar - entry_bar); CLOSE: the "
                       "official close"),
        "reconciliation": ("BOOK_TRADE_RECONCILIATION unless, for every v1 trade, lab.score(trade, day, spy_day, "
                           "f, spy_f, 1.0) on this module's own candidate (the dict lab.extension_fade returned on "
                           "its own day, f, SPY day and SPY features) gives the lab record's reason, minutes and "
                           "entry_bar, and |residual x 1e4 - gross_bps| <= 1e-6; this pins the betas and exit bars "
                           "the hedge uses"),
    },
    "candidates": {
        "stock_day_pass": ("this module's own, mirroring ext_fade_overlays.stock_days exactly: the same warm-up month "
                           "(ir.previous_month), ir.detection_months, ir.spy_features, ir.daily_features with "
                           "ir.off_rows, ir.History updated after every session, ir.CORPORATE_ACTIONS and "
                           "ir.eligibility; it receives the daily price rows uncut"),
        "C_t": ("every stock-day of session t that meets condition 1 or 2, and condition 3"),
        "condition_1": "ir.eligibility(...) is None",
        "condition_2": ("ir.eligibility(...) is DAILY_ROW_ERROR or DAILY_CLOSE_MISMATCH, and f, day['o'][0], the "
                        "session's SPY day and its SPY features all exist (a late-excluded candidate: those checks "
                        "read the session's own daily close and last minute, or the next session's open)"),
        "condition_3": ("lab.extension_fade(day, f, day['o'][0] / f['prior_close'] - 1, lab.VARIANTS['EXT-FADE.v1'], "
                        "{'spy': spy_day, 'spy_f': spy_f, 'timing': None}) returns a trade, or a skip other than "
                        "MISSING_SPY_OPEN (RULE_201, NO_ENTRY_MINUTE, ENTRY_BEYOND_TARGET)"),
        "budget": ("late-excluded candidates, skipped candidates and trades the lab later drops as UNSCORABLE all use "
                   "their share of the cap budget and never trade; that budget is never reallocated"),
        "reads": ("the 09:30 open (the gap); prior sessions' daily rows and minute bars; the universe, listing dates "
                  "and corporate-action dates; and the session's official open (the PRICE_MISMATCH check). Through "
                  "condition 2, membership does not depend on any check that reads data after 09:30. A stock-day "
                  "with no daily row for the session itself has no features and is never a candidate, as in v1."),
        "reconciliations": {
            "ELIGIBILITY_DISAGREES_WITH_THE_LAB": ("the eligible counts per session under condition 1 equal "
                                                   "run['eligible']"),
            "CANDIDATES_DISAGREE_WITH_THE_LAB": ("the candidates that end as lab trades are exactly v1's check "
                                                 "trades, the same (ticker, session) set and the same entry_bar; and "
                                                 "the skips counted on eligible stock-days equal the lab's"),
            "reported": "the number of late-excluded candidates",
        },
    },
    "sizing": {
        "unit": "one base notional N = 1, v1's equal per-trade notional; every candidate has base size 1",
        "cluster": "cluster(i) = (group(ticker_i), side_i), group from SECTOR_MAP",
        "when": "once per session at 09:31, from C_t only",
        "scale_down": ["n_c = the number of candidates in cluster c",
                       "a_c = min(1, K / n_c)",
                       "g_s = the sum of a_c x n_c over the clusters c in group s; b_s = min(1, S / g_s)",
                       "g = the sum of b_s x g_s over the groups; d = min(1, G / g)",
                       "s_i = a_c(i) x b_s(i) x d; a traded candidate's position is x_i = s_i"],
        "properties": ("candidate gross at most K per cluster, S per group and G per session; within each layer every "
                       "affected position shrinks by the same factor; s_i <= 1, so positions never grow and the trade "
                       "set stays v1's"),
        "per_stock": "at most one position per name per session (v1's rule) and s_i <= 1; k x s_i <= k <= K_MAX",
        "v1_book": "x_i = 1 for every v1 trade",
    },
    "hedge": {
        "per_trade": "h_i = -side_i x beta_i x x_i, in SPY dollars per base notional at the trade's entry SPY price",
        "aggregate": ("one SPY position H, held in shares: each trade's hedge shares are added at its entry event and "
                      "removed at its exit event, with no rebalancing in between, so H(t) is the sum of the open "
                      "trades' h_i at every minute and no hedge outlives its trade"),
        "events": ("E(b): the entries filled at bar b's open; X(b): the TARGET exits during bar b; CLOSE: one MOC "
                   "order for every CLOSE exit. An entry and an exit in the same bar are separate events."),
        "traded": ("at each event e the book trades dH_e = the sum of h_i over the entries at e - the sum of h_i over "
                   "the exits at e, valued at the trades' entry SPY prices"),
        "pnl": ("each trade's own lab hedge leg, x_i x (-side_i x beta_i x (S_out / S_in - 1)), with the lab's SPY "
                "prices; it is already inside g_i, so netting changes only the SPY traded"),
        "convention": ("within one exit bar a gapped exit's leg uses SPY's open and a non-gapped exit's leg SPY's mid, "
                       "while the book trades one netted order: the lab's own convention"),
        "costs": ("SPY: spy_cost_bps x the sum over the session's events of |dH_e|; stock: cost_bps a side on x_i at "
                  "entry and at exit; both books pay both (v1's lab figures do not charge the SPY leg)"),
    },
    "pnl": {
        "sessions": "S = sorted(run['eligible']) of the check; a session with no trade has P_t = 0",
        "P_t": ("the sum over the trades i of session t of x_i x (g_i - 2c), minus c_SPY x the sum over the session's "
                "events e of |dH_e|, in bps of one base notional"),
        "G_t": "the sum of x_i over the trades of session t: the entry gross (the hedge is not counted)",
        "reconciliations": {
            "V1_BOOK_DISAGREES_WITH_THE_LAB": ("the v1 book with c_SPY = 0 sums to the sum of the lab's net_bps[5] "
                                               "(within 1e-6 bps)"),
            "V1_CHECK_NOT_REPRODUCED": "ext_fade_overlays.v1_reproduced(v1, entry) is True",
        },
    },
    "normalisation": {
        "k": ("k = the sum over t of G_t(v1) / the sum over t of G_t(CB) on the check sessions; P^N_t = k x P_t(CB); "
              "every risk comparison is P^N against P(v1); k uses exposure only, never P&L"),
        "feasibility": "k <= K_MAX",
        "reported": ["k_cal, frozen from 2016-19 exposure: the version tradable ex ante",
                     "time-weighted gross: notional x (minutes + 1)",
                     "equal volatility: k_vol = sd(v1) / sd(CB), with ES5/sd, MDD/sd and worst/sd",
                     "the unnormalised CB (k = 1)"],
    },
    "metrics": {
        "Sharpe": "mean / sd(ddof = 1) x sqrt(252)",
        "ES5": "-(the mean of the m smallest x_t), m = ceil(es_level x T)",
        "W10": "the mean of the w10 smallest x_t; the single worst day is also reported",
        "DD": "sqrt(the mean over t of min(x_t, 0)^2)",
        "MDD": ("the minimum over t of (C_t - the maximum over u <= t of C_u), with C_0 = 0 and C_t = x_1 + ... + x_t: "
                "additive, fixed notional, no compounding"),
    },
    "intervals": {
        "method": "a paired circular block bootstrap of sessions",
        "rng": "numpy.random.default_rng(seed)",
        "starts": "rng.integers(0, T, size=(reps, ceil(T / block))), drawn in ONE call",
        "idx": ("((starts[:, :, None] + numpy.arange(block)[None, None, :]) % T).reshape(reps, -1)[:, :T]: each "
                "replicate takes block consecutive sessions from each start, wrapping at the end, concatenated in draw "
                "order and truncated to T"),
        "paired": ("both books and every gating statistic use this one idx matrix; in replicate b, k_b = the sum of "
                   "G_v1[idx_b] / the sum of G_CB[idx_b]"),
        "statistics": ("R_ES = ES5(P^N) / ES5(v1); dSR = SR(CB) - SR(v1); the ratios for W10, DD and MDD, reported "
                       "with 90% intervals"),
        "bounds": ("UB95 is the 95th percentile and LB95 the 5th (numpy.percentile, linear); a bound over a replicate "
                   "whose statistic is undefined is undefined, and an undefined bound fails its gate"),
        "stress": ("stress bootstraps (leave-one-year-out and others) use the same construction and seed with their "
                   "own T"),
    },
    "sector_map": {
        "level": "13 GICS industry groups",
        "assignment": ("each ticker's GICS industry group under the structure in force from 2018-09-28 to "
                       "2023-03-17, as of 2019-12-31, the last day before the test; names listed later take their "
                       "first assignment under that structure. The 2023-03-17 GICS change (TGT, BKNG, AMZN, "
                       "COST/WMT, SOFI, UBER) is deliberately not applied: one frozen map."),
        "not_used": "stock_machine/sectors.py (SIC ranges)",
        "least_certain": "ABNB (placed with BKNG), HIMS (a singleton either way) and UBER (Industrials / Road & Rail)",
        "groups": GROUP_NAMES,
        "map": SECTOR_MAP,
        "UNMAPPED_TICKER": "the runner refuses if any run ticker is missing from the map",
    },
    "constants": CONSTANTS,
    "pass_bar": {
        "gates": PASS_BAR,
        "wording": {
            "at": ("c = 5 bps a side on stock and c_SPY = 0.5 bps a side, at equal average gross (P^N against v1), on "
                   "2020-01..2023-12"),
            "G1": "feasibility: k <= 2.5",
            "G2": "tail (primary): R_ES < 1, AND its UB95 < 1",
            "G3": ("consistency (point estimates, all three): W10(P^N) is less severe than W10(v1); DD(P^N) < DD(v1); "
                   "and MDD(P^N) is shallower than MDD(v1)"),
            "G4": "Sharpe non-inferiority: LB95 of dSR > -0.30",
            "verdict": ("PASS if and only if G1, G2, G3 and G4 all hold (intersection-union: the joint false-pass rate "
                        "is at most 5%). The result counts only if v1's check verdict is PASS; there is no standalone "
                        "path: if v1 fails, the CB result is recorded and does not go forward."),
        },
    },
    "stress": STRESS,
    "use": {
        "pass": ("PASS, with v1 PASS: the CB enters the 2024+ hold-out as its own pre-registered secondary hypothesis "
                 "to v1, in its own family. It does not join, and does not change, the three overlays' registered "
                 "Holm family. Its hold-out test is this frozen spec and pass bar, unchanged, at one-sided 5%, "
                 "registered before any 2024 bar is read."),
        "fail": ("FAIL: final for these data, and v1 stays on its plain equal-notional book. Any change to a constant, "
                 "the map, the nesting or the normalisation is a new variant, testable only on new data or new "
                 "universes."),
    },
}


def spec_sha() -> str:
    """The whole spec, its wording included. Registered: any edit to the text refuses the run (SPECS_NOT_REGISTERED)."""
    return digest(SPEC)


def params_of() -> dict:
    """The parameters alone: the constants, the sector map and the pass bar. No wording."""
    return {"constants": CONSTANTS, "sector_map": SECTOR_MAP, "pass_bar": PASS_BAR}


def params_sha() -> str:
    return digest(params_of())


def map_sha() -> str:
    return digest(SECTOR_MAP)


def file_sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def module_sha() -> str:
    """This file as it is on disk now."""
    return file_sha(__file__)


MODULE_SHA = module_sha()               # the code this process imported: what a logged check was produced by


# ---- the stock-day pass and the 09:31 candidates ------------------------------------------------------------------

def side_of(gap: float) -> int:
    return -1 if gap > 0 else 1


def stock_days(bars_for, prices: dict, months: tuple[str, str], tickers: list[str]) -> dict:
    """The run's stock-days judged exactly as lab.replay judges them (ext_fade_overlays.stock_days' conventions), with
    the 09:31 candidates C_t: every stock-day that is eligible (condition 1) or late-excluded (condition 2) on which
    v1's own setup returns a trade or an entry-time skip other than MISSING_SPY_OPEN (condition 3). Each candidate
    keeps its minute bars, daily features, SPY day and SPY features, and, when eligible, whether the lab can score
    it. `skips` counts v1's skips on eligible stock-days the way the lab counts them."""
    if months[0] > months[1]:
        raise ValueError("EXT_FADE_PORTFOLIO_MONTHS_INVERTED")
    if months[1] >= HOLDOUT_FROM:
        raise ValueError("EXT_FADE_PORTFOLIO_NEVER_READS_THE_HOLDOUT")
    spy_rows = prices["SPY"]
    loaded = [ir.previous_month(months[0])] + ir.months_between(*months)
    detect = ir.detection_months(months)
    spy_seen = {m: bars_for("SPY", m) for m in detect}
    spy_days = {s: d for m in loaded for s, d in spy_seen[m].items()}
    spy_feat, spy_bad = ir.spy_features(spy_rows, {s: d for m in detect for s, d in spy_seen[m].items()})
    eligible, skips, candidates = defaultdict(int), defaultdict(int), []
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
                    f, spy_day, spy_f = feats.get(session), spy_days.get(session), spy_feat.get(session)
                    why = ir.eligibility(ticker, session, day, f, hist, spy_day, spy_f,
                                         ir.CORPORATE_ACTIONS.get(ticker, ()), daily)
                    if why is None:
                        eligible[session] += 1
                    late = why in LATE and f is not None and bool(day["o"]) and day["o"][0] is not None \
                        and spy_day is not None and spy_f is not None
                    if why is None or late:
                        c = _candidate(ticker, session, day, f, spy_day, spy_f, hist if why is None else None, skips)
                        if c is not None:
                            c["late"] = why if late else None
                            candidates.append(c)
                hist.update(day, session)
    return {"months": list(months), "eligible": dict(eligible), "skips": dict(skips), "candidates": candidates}


def _candidate(ticker, session, day, f, spy_day, spy_f, hist, skips) -> dict | None:
    """v1's setup at 09:31 on one stock-day. hist is given on an eligible stock-day only: then the lab's skips are
    counted and a trade's scorability is judged as the lab judges it (lab._score_pair, UNSCORABLE when None)."""
    gap = day["o"][0] / f["prior_close"] - 1
    t = lab.extension_fade(day, f, gap, BASE_SPEC, {"spy": spy_day, "spy_f": spy_f, "timing": None})
    if t is None:
        return None
    kind = t.get("skip", TRADE)
    scorable = None
    if hist is not None:
        if kind == TRADE:
            scorable = lab._score_pair(t, day, spy_day, f, spy_f, hist, BASE_SPEC.get("scale", lab.SCALE_WINDOW)) \
                is not None
            if not scorable:
                skips["UNSCORABLE"] += 1
        else:
            skips[kind] += 1
    if kind == "MISSING_SPY_OPEN":
        return None
    return {"ticker": ticker, "session": session, "gap": gap, "side": side_of(gap), "kind": kind,
            "scorable": scorable, "trade": t if kind == TRADE else None,
            "day": day, "f": f, "spy": spy_day, "spy_f": spy_f}


def ends_as_trade(c: dict) -> bool:
    """An eligible candidate the lab scores: one of v1's trades."""
    return c["late"] is None and c["kind"] == TRADE and bool(c["scorable"])


def marks(c: dict, exit_bar: int | None, final: float) -> np.ndarray:
    """A trade's hedged mark, side x (close / entry - 1) - side x beta x (SPY close / SPY at entry - 1), at the close
    of every minute from its entry bar (the last traded close carried through a missing minute), frozen at `final`
    (its residual) from its exit bar on, then the official close; NaN before the entry."""
    day, spy, t = c["day"], c["spy"], c["trade"]
    side, beta, b0, entry = t["side"], c["f"]["beta"], t["entry_bar"], t["entry"]
    s_in = spy["o"][b0]
    out = np.full(POINTS, np.nan)
    px, spx = entry, s_in
    for b in range(b0, POINTS - 1):
        if exit_bar is not None and b >= exit_bar:
            out[b] = final
            continue
        if b < len(day["c"]) and day["c"][b] is not None:
            px = day["c"][b]
        if b < len(spy["c"]) and spy["c"][b] is not None:
            spx = spy["c"][b]
        out[b] = side * (px / entry - 1) - side * beta * (spx / s_in - 1)
    out[POINTS - 1] = final
    return out


def reconcile(run: dict, v1: list[dict], days: dict) -> tuple[list[dict] | None, str]:
    """The stock-day pass against the lab: eligibility, the candidates that end as trades and every trade re-scored.
    Returns the book's trades (one per v1 trade, in the lab's order) or None and the reason."""
    if days["eligible"] != run["eligible"]:
        return None, "ELIGIBILITY_DISAGREES_WITH_THE_LAB"
    lab_skips = {k.split(":", 1)[1]: v for k, v in run["skips"].items() if k.startswith(BASE + ":")}
    if days["skips"] != lab_skips:
        return None, "CANDIDATES_DISAGREE_WITH_THE_LAB"
    ends = {(c["ticker"], c["session"]): c for c in days["candidates"] if ends_as_trade(c)}
    by_key = {(t["ticker"], t["session"]): t for t in v1}
    if len(by_key) != len(v1) or set(ends) != set(by_key) or \
            any(ends[k]["trade"]["entry_bar"] != by_key[k]["entry_bar"] for k in ends):
        return None, "CANDIDATES_DISAGREE_WITH_THE_LAB"
    if any(c["ticker"] not in SECTOR_MAP for c in days["candidates"]):
        return None, "UNMAPPED_TICKER"
    trades = []
    for t in v1:
        c = ends[(t["ticker"], t["session"])]
        got = lab.score(c["trade"], c["day"], c["spy"], c["f"], c["spy_f"], 1.0)
        if got is None or got["reason"] not in ("TARGET", "CLOSE") or got["reason"] != t["reason"] or \
                got["minutes"] != t["minutes"] or c["trade"]["entry_bar"] != t["entry_bar"] or \
                c["gap"] != t["gap"] or c["trade"]["side"] != c["side"] or \
                not abs(got["residual"] * 1e4 - t["gross_bps"]) <= 1e-6:
            return None, "BOOK_TRADE_RECONCILIATION"
        exit_bar = t["entry_bar"] + t["minutes"] if t["reason"] == "TARGET" else None
        trades.append({"ticker": t["ticker"], "session": t["session"], "side": c["side"], "beta": c["f"]["beta"],
                       "entry_bar": t["entry_bar"], "exit_bar": exit_bar, "reason": t["reason"],
                       "minutes": t["minutes"], "gross_bps": t["gross_bps"], "net_bps_at_5": t["net_bps"][5],
                       "residual_diff_bps": abs(got["residual"] * 1e4 - t["gross_bps"]),
                       "marks": marks(c, exit_bar, t["gross_bps"] / 1e4)})
    return trades, "OK"


# ---- sizing --------------------------------------------------------------------------------------------------------

def scale_down(candidates: list[dict], *, K=None, S=None, G=None, sector_map=None) -> tuple[dict, dict]:
    """The nested proportional scale-down, once per session from its candidates only: s_i for every candidate,
    keyed by (ticker, session), and each session's layer factors and the candidate gross each layer removed."""
    K = CONSTANTS["K"] if K is None else K
    S = CONSTANTS["S"] if S is None else S
    G = CONSTANTS["G"] if G is None else G
    group = SECTOR_MAP if sector_map is None else sector_map
    by = defaultdict(list)
    for c in candidates:
        by[c["session"]].append(c)
    s_of, sessions = {}, {}
    for session in sorted(by):
        cs = by[session]
        if len({c["ticker"] for c in cs}) != len(cs):
            raise ValueError(f"EXT_FADE_PORTFOLIO_TWO_POSITIONS_IN_ONE_NAME {session}")
        n = defaultdict(int)
        for c in cs:
            n[(group[c["ticker"]], c["side"])] += 1
        a = {cl: min(1.0, K / v) for cl, v in n.items()}
        g_s = defaultdict(float)
        for cl, v in n.items():
            g_s[cl[0]] += a[cl] * v
        b = {grp: min(1.0, S / x) for grp, x in g_s.items()}
        total = math.fsum(b[grp] * x for grp, x in g_s.items())
        d = min(1.0, G / total)
        for c in cs:
            cl = (group[c["ticker"]], c["side"])
            s_of[(c["ticker"], session)] = a[cl] * b[cl[0]] * d
        after_a = math.fsum(g_s.values())
        sessions[session] = {"candidates": len(cs), "a": {f"{g}:{s:+d}": x for (g, s), x in a.items()},
                             "b": dict(b), "d": d, "cluster_binds": any(x < 1 for x in a.values()),
                             "group_binds": any(x < 1 for x in b.values()), "book_binds": d < 1,
                             "removed": {"cluster": len(cs) - after_a, "group": after_a - total,
                                         "book": total - d * total}}
    return s_of, sessions


# ---- the book -------------------------------------------------------------------------------------------------------

def hedge_events(trades: list[dict], x: list[float]) -> dict:
    """The SPY the book trades at each event, dH_e, in SPY dollars per base notional at the trades' entry prices:
    (session, "ENTRY", bar), (session, "EXIT", bar) for TARGET exits, (session, "CLOSE", None) for the MOC."""
    events = defaultdict(list)
    for t, xi in zip(trades, x):
        h = -t["side"] * t["beta"] * xi
        events[(t["session"], "ENTRY", t["entry_bar"])].append(h)
        out = (t["session"], "EXIT", t["exit_bar"]) if t["exit_bar"] is not None else (t["session"], "CLOSE", None)
        events[out].append(-h)
    return {e: math.fsum(hs) for e, hs in events.items()}


def book(trades: list[dict], x: list[float], sessions: list[str], *, cost_bps: float, spy_cost_bps: float) -> dict:
    """One book's daily P&L in bps of one base notional and its entry gross, on every session of `sessions` (0 when
    nothing trades): P_t = sum x_i (g_i - 2c) - c_SPY sum_e |dH_e|, G_t = sum x_i."""
    pnl, gross, spy, unnetted = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for t, xi in zip(trades, x):
        pnl[t["session"]].append(xi * (t["gross_bps"] - 2 * cost_bps))
        gross[t["session"]].append(xi)
        unnetted[t["session"]].append(2 * abs(t["side"] * t["beta"] * xi))
    for (session, _, _), dh in hedge_events(trades, x).items():
        spy[session].append(abs(dh))
    traded = np.array([math.fsum(spy[s]) for s in sessions])
    return {"pnl": np.array([math.fsum(pnl[s]) for s in sessions]) - spy_cost_bps * traded,
            "gross": np.array([math.fsum(gross[s]) for s in sessions]), "spy_traded": traded,
            "spy_unnetted": np.array([math.fsum(unnetted[s]) for s in sessions])}


def troughs(trades: list[dict], x: list[float], sessions: list[str], cost_bps: float) -> np.ndarray:
    """Each session's intraday trough: the minimum over its points of the sum of x_i x (the hedged mark - 2c), in
    bps, each trade frozen at its exit and counted from its entry; never above 0 (the book starts the day flat)."""
    by = defaultdict(lambda: np.zeros(POINTS))
    for t, xi in zip(trades, x):
        m = t["marks"]
        by[t["session"]] += np.where(np.isnan(m), 0.0, xi * (m * 1e4 - 2 * cost_bps))
    return np.array([min(0.0, float(by[s].min())) if s in by else 0.0 for s in sessions])


# ---- metrics and intervals -----------------------------------------------------------------------------------------

def _f(x):
    return None if x is None or not math.isfinite(x) else float(x)


def metrics(x) -> dict:
    """Sharpe, ES5, W10, worst day, DD and MDD of one daily series (bps)."""
    x = np.asarray(x, dtype=float)
    T = x.size
    q = np.sort(x)
    m = math.ceil(CONSTANTS["es_level"] * T)
    sd = float(x.std(ddof=1)) if T > 1 else None
    cum = np.cumsum(x)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    mean = float(x.mean()) if T else None
    return {"T": T, "mean": mean, "sd": sd,
            "sharpe": mean / sd * math.sqrt(252) if sd else None,
            "es5": float(-q[:m].mean()) if m else None,
            "w10": float(q[:CONSTANTS["w10"]].mean()) if T >= CONSTANTS["w10"] else None,
            "worst": float(q[0]) if T else None,
            "dd": float(math.sqrt(np.mean(np.minimum(x, 0.0) ** 2))) if T else None,
            "mdd": float((cum - peak).min()) if T else None, "total": float(cum[-1]) if T else None}


def _ratio(a, b):
    return a / b if a is not None and b not in (None, 0) else None


def ratios(m1: dict, mc: dict) -> dict:
    """The CB's figures over v1's. R_ES needs v1's ES5 above 0."""
    return {"r_es": _ratio(mc["es5"], m1["es5"]) if m1["es5"] and m1["es5"] > 0 else None,
            "w10": _ratio(mc["w10"], m1["w10"]), "dd": _ratio(mc["dd"], m1["dd"]), "mdd": _ratio(mc["mdd"], m1["mdd"]),
            "worst": _ratio(mc["worst"], m1["worst"]),
            "dsr": mc["sharpe"] - m1["sharpe"] if mc["sharpe"] is not None and m1["sharpe"] is not None else None}


def boot_index(T: int, *, seed=None, reps=None, block=None) -> np.ndarray:
    """The paired circular block bootstrap's session index: starts drawn in one call."""
    seed = CONSTANTS["seed"] if seed is None else seed
    reps = CONSTANTS["reps"] if reps is None else reps
    block = CONSTANTS["block"] if block is None else block
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, T, size=(reps, math.ceil(T / block)))
    return ((starts[:, :, None] + np.arange(block)[None, None, :]) % T).reshape(reps, -1)[:, :T]


def bootstrap(v1, g_v1, cb, g_cb, idx=None, *, chunk: int = 500) -> dict:
    """The paired replicates of R_ES, dSR and the W10, DD and MDD ratios, with k_b recomputed in each replicate."""
    v1, g_v1, cb, g_cb = (np.asarray(a, dtype=float) for a in (v1, g_v1, cb, g_cb))
    T = v1.size
    idx = boot_index(T) if idx is None else idx
    reps = idx.shape[0]
    m, w = math.ceil(CONSTANTS["es_level"] * T), CONSTANTS["w10"]
    stats = {k: np.empty(reps) for k in ("r_es", "dsr", "w10", "dd", "mdd", "k")}
    with np.errstate(divide="ignore", invalid="ignore"):
        for lo in range(0, reps, chunk):
            I = idx[lo:lo + chunk]
            V = v1[I]
            kb = g_v1[I].sum(1) / g_cb[I].sum(1)
            C = cb[I] * kb[:, None]
            sv, sc = np.sort(V, 1), np.sort(C, 1)
            es_v, es_c = -sv[:, :m].mean(1), -sc[:, :m].mean(1)
            stats["r_es"][lo:lo + chunk] = np.where(es_v > 0, es_c / es_v, np.nan)
            stats["dsr"][lo:lo + chunk] = (C.mean(1) / C.std(1, ddof=1) - V.mean(1) / V.std(1, ddof=1)) * math.sqrt(252)
            stats["w10"][lo:lo + chunk] = sc[:, :w].mean(1) / sv[:, :w].mean(1)
            stats["dd"][lo:lo + chunk] = np.sqrt(np.mean(np.minimum(C, 0) ** 2, 1)) / \
                np.sqrt(np.mean(np.minimum(V, 0) ** 2, 1))
            stats["mdd"][lo:lo + chunk] = _mdd_rows(C) / _mdd_rows(V)
            stats["k"][lo:lo + chunk] = kb

    def pct(name, q):
        a = stats[name]
        return float(np.percentile(a, q)) if np.isfinite(a).all() else None
    return {"reps": reps, "T": T, "block": CONSTANTS["block"], "seed": CONSTANTS["seed"],
            "ub95_r_es": pct("r_es", PASS_BAR["G2"]["ub95_percentile"]),
            "lb95_dsr": pct("dsr", PASS_BAR["G4"]["lb95_percentile"]),
            "ci90": {k: [pct(k, 5), pct(k, 95)] for k in ("r_es", "dsr", "w10", "dd", "mdd", "k")},
            "median_r_es": pct("r_es", 50),
            "sd": {k: _f(float(np.std(stats[k]))) if np.isfinite(stats[k]).all() else None for k in ("r_es", "dsr")},
            "undefined": {k: int((~np.isfinite(a)).sum()) for k, a in stats.items()}}


def _mdd_rows(X):
    cum = np.cumsum(X, 1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((X.shape[0], 1)), cum], 1), 1)[:, 1:]
    return (cum - peak).min(1)


def compare(v1: dict, cb: dict, *, k: float | None = None, idx=None, boot: bool = True) -> dict:
    """v1's daily series against the CB's at a normalisation k (default: equal average entry gross)."""
    g1, gc = float(v1["gross"].sum()), float(cb["gross"].sum())
    k_eq = g1 / gc if gc > 0 else None
    k_used = k_eq if k is None else k
    m1 = metrics(v1["pnl"])
    mc = metrics(k_used * cb["pnl"]) if k_used is not None else {key: None for key in m1}
    out = {"sessions": int(v1["pnl"].size), "k": k_used, "k_equal_gross": k_eq, "v1": m1, "cb": mc,
           "ratios": ratios(m1, mc) if k_used is not None else {}}
    if boot and k_eq is not None:
        out["bootstrap"] = bootstrap(v1["pnl"], v1["gross"], cb["pnl"], cb["gross"], idx)
    return out


def gates(c: dict) -> dict:
    """The pass bar on a comparison at equal average gross."""
    bar, r, m1, mc = PASS_BAR, c.get("ratios", {}), c["v1"], c["cb"]
    boot = c.get("bootstrap", {})
    k, r_es, ub, lb = c["k"], r.get("r_es"), boot.get("ub95_r_es"), boot.get("lb95_dsr")
    g1 = k is not None and k <= bar["G1"]["k_at_most"]
    g2 = r_es is not None and ub is not None and r_es < bar["G2"]["r_es_below"] and ub < bar["G2"]["ub95_below"]
    parts = {"w10": mc["w10"] is not None and m1["w10"] is not None and mc["w10"] > m1["w10"],
             "dd": mc["dd"] is not None and m1["dd"] is not None and mc["dd"] < m1["dd"],
             "mdd": mc["mdd"] is not None and m1["mdd"] is not None and mc["mdd"] > m1["mdd"]}
    g4 = lb is not None and lb > bar["G4"]["lb95_dsr_above"]
    out = {"G1": {"k": k, "pass": g1}, "G2": {"r_es": r_es, "ub95": ub, "pass": g2},
           "G3": {**parts, "pass": all(parts.values())}, "G4": {"dsr": r.get("dsr"), "lb95": lb, "pass": g4}}
    out["verdict"] = "PASS" if all(out[g]["pass"] for g in bar["pass"]) else "FAIL"
    return out


# ---- the evaluation ------------------------------------------------------------------------------------------------

def _mask(sessions, keep):
    return np.array([keep(s) for s in sessions], dtype=bool)


def _sub(b: dict, mask) -> dict:
    return {k: v[mask] for k, v in b.items()}


def _per_unit(cost_total: float, units: float):
    return cost_total / units if units else None


def evaluate(trades: list[dict], candidates: list[dict], sessions: list[str], years, *, full: bool = True) -> tuple:
    """EXT-FADE-CB.v1 against v1 on one period: the pass bar and, with full, every reported table. trades: the
    reconciled v1 trades (reconcile); candidates: C_t (stock_days); sessions: sorted(run['eligible']). Returns
    (result, "OK") or (None, the reason) when the v1 book does not reconcile with the lab."""
    years = tuple(years)
    c, c_spy = PASS_BAR["cost_bps"], PASS_BAR["spy_cost_bps"]
    s_of, diag = scale_down(candidates)
    x_cb = [s_of[(t["ticker"], t["session"])] for t in trades]
    ones = [1.0] * len(trades)
    lab_total = math.fsum(t["net_bps_at_5"] for t in trades)
    v1_free = book(trades, ones, sessions, cost_bps=c, spy_cost_bps=0.0)
    if not abs(math.fsum(v1_free["pnl"]) - lab_total) <= 1e-6:
        return None, "V1_BOOK_DISAGREES_WITH_THE_LAB"
    v1 = book(trades, ones, sessions, cost_bps=c, spy_cost_bps=c_spy)
    cb = book(trades, x_cb, sessions, cost_bps=c, spy_cost_bps=c_spy)
    T = len(sessions)
    idx = boot_index(T) if T else None
    main = compare(v1, cb, idx=idx)
    k = main["k"]
    cand_kinds = defaultdict(int)
    for cand in candidates:
        cand_kinds[cand["kind"]] += 1
    late = [cand for cand in candidates if cand["late"]]
    units_v1, units_cb = float(len(trades)), math.fsum(x_cb)
    cb_free = book(trades, x_cb, sessions, cost_bps=c, spy_cost_bps=0.0)
    result = {
        "spec_id": SPEC_ID, "years": list(years), "sessions": T, "trades": len(trades),
        "active_sessions": int((v1["gross"] > 0).sum()),
        "candidates": {"count": len(candidates), "sessions": len({x["session"] for x in candidates}),
                       "kinds": dict(sorted(cand_kinds.items())),
                       "end_as_trades": sum(1 for x in candidates if ends_as_trade(x)),
                       "unscorable": sum(1 for x in candidates if x["late"] is None and x["kind"] == TRADE
                                         and not x["scorable"]),
                       "late_excluded": len(late),
                       "late_excluded_by_reason": {r: sum(1 for x in late if x["late"] == r) for r in LATE},
                       "late_excluded_sessions": sorted({x["session"] for x in late})},
        "reconciliation": {"rescored": len(trades),
                           "max_residual_diff_bps": max((t["residual_diff_bps"] for t in trades), default=0.0),
                           "v1_total_c_spy_0": math.fsum(v1_free["pnl"]), "lab_net_total_at_5": lab_total},
        "hedge": {"spy_cost_per_unit": {"v1": _per_unit(math.fsum(v1_free["pnl"]) - math.fsum(v1["pnl"]), units_v1),
                                        "cb": _per_unit(math.fsum(cb_free["pnl"]) - math.fsum(cb["pnl"]), units_cb)},
                  "unnetted_per_unit": {"v1": _per_unit(c_spy * math.fsum(v1["spy_unnetted"]), units_v1),
                                        "cb": _per_unit(c_spy * math.fsum(cb["spy_unnetted"]), units_cb)},
                  "events": len(hedge_events(trades, ones)),
                  "same_bar_entry_and_exit": sum(1 for t in trades if t["exit_bar"] == t["entry_bar"]),
                  "one_sided_sessions_share": _one_sided_share(trades)},
        "k": k, "k_cal": CONSTANTS["k_cal"], "mean_gross": {"v1": _f(float(v1["gross"].mean())) if T else None,
                                                            "cb": _f(float(cb["gross"].mean())) if T else None},
        "comparison": main,
    }
    result["gates"] = gates(main)
    result["verdict"] = result["gates"]["verdict"]
    if not full:
        return result, "OK"
    result["binding"] = _binding(diag, len(candidates), x_cb)
    result["normalisations"] = _normalisations(trades, x_cb, v1, cb, k)
    result["stress"] = {"costs": {}}
    for cost in STRESS["stock_costs_bps"]:
        b1 = book(trades, ones, sessions, cost_bps=cost, spy_cost_bps=c_spy)
        b2 = book(trades, x_cb, sessions, cost_bps=cost, spy_cost_bps=c_spy)
        result["stress"]["costs"][f"stock_{cost}_bps"] = _brief(compare(b1, b2, idx=idx))
    for cost in STRESS["spy_costs_bps"]:
        b1 = book(trades, ones, sessions, cost_bps=c, spy_cost_bps=cost)
        b2 = book(trades, x_cb, sessions, cost_bps=c, spy_cost_bps=cost)
        result["stress"]["costs"][f"spy_{cost}_bps"] = _brief(compare(b1, b2, idx=idx))
    post = [x for x in candidates if ends_as_trade(x)]
    s_post, _ = scale_down(post)
    x_post = [s_post[(t["ticker"], t["session"])] for t in trades]
    result["stress"]["post_skip_candidates"] = {
        **_brief(compare(v1, book(trades, x_post, sessions, cost_bps=c, spy_cost_bps=c_spy), idx=idx)),
        "positions_that_differ": sum(1 for a, b in zip(x_cb, x_post) if abs(a - b) > 1e-12)}
    result["stress"]["leave_one_year_out"] = {}
    result["stress"]["by_year"] = {}
    for y in years:
        out = _mask(sessions, lambda s: s[:4] != y)
        inside = ~out
        if out.any():
            result["stress"]["leave_one_year_out"][y] = _brief(compare(_sub(v1, out), _sub(cb, out)))
        if inside.any():
            b1, b2 = _sub(v1, inside), _sub(cb, inside)
            m1, mc = metrics(b1["pnl"]), metrics(k * b2["pnl"]) if k is not None else None
            result["stress"]["by_year"][y] = {
                "sessions": int(inside.sum()), "v1": m1, "cb": mc, "ratios": ratios(m1, mc) if mc else {},
                "capped_sessions": int(sum(1 for a, b in zip(b1["gross"], b2["gross"]) if b < a - 1e-12))}
    t_v1, t_cb = troughs(trades, ones, sessions, c), troughs(trades, x_cb, sessions, c)
    m = math.ceil(CONSTANTS["es_level"] * T) if T else 0
    if k is not None and T:
        result["stress"]["intraday_trough"] = {
            "es5": {"v1": float(-np.sort(t_v1)[:m].mean()), "cb": float(-np.sort(k * t_cb)[:m].mean())},
            "worst": {"v1": float(t_v1.min()), "cb": float(k * t_cb.min())}}
        active = v1["gross"] > 0
        result["stress"]["gross"] = {
            "peak": {"v1": float(v1["gross"].max()), "cb": float(k * cb["gross"].max())},
            "p99_of_active_sessions": {"v1": float(np.percentile(v1["gross"][active], 99)) if active.any() else None,
                                       "cb": float(k * np.percentile(cb["gross"][cb["gross"] > 0], 99))
                                       if (cb["gross"] > 0).any() else None}}
        result["stress"]["concentration"] = _concentration(trades, x_cb, v1, cb, k, c)
    return result, "OK"


def _one_sided_share(trades: list[dict]) -> float | None:
    by = defaultdict(set)
    for t in trades:
        by[t["session"]].add(t["side"])
    return sum(1 for t in trades if len(by[t["session"]]) == 1) / len(trades) if trades else None


def _brief(c: dict) -> dict:
    """A comparison's headline figures and gates."""
    b = c.get("bootstrap", {})
    return {"sessions": c["sessions"], "k": c["k"],
            "v1": {k: c["v1"][k] for k in ("sharpe", "es5", "w10", "dd", "mdd", "worst", "mean")},
            "cb": {k: c["cb"][k] for k in ("sharpe", "es5", "w10", "dd", "mdd", "worst", "mean")},
            "ratios": c.get("ratios", {}), "ub95_r_es": b.get("ub95_r_es"), "lb95_dsr": b.get("lb95_dsr"),
            "gates": gates(c)}


def _binding(diag: dict, n_candidates: int, x_cb: list[float]) -> dict:
    removed = {layer: math.fsum(d["removed"][layer] for d in diag.values()) for layer in ("cluster", "group", "book")}
    s = np.array(x_cb) if x_cb else np.array([np.nan])
    return {"candidate_sessions": len(diag),
            "sessions_binding": {layer: sum(1 for d in diag.values() if d[f"{layer}_binds"])
                                 for layer in ("cluster", "group", "book")},
            "sessions_any_layer": sum(1 for d in diag.values() if d["cluster_binds"] or d["group_binds"]
                                      or d["book_binds"]),
            "share_of_candidate_gross_removed": {k: v / n_candidates if n_candidates else None
                                                 for k, v in removed.items()},
            "positions": {"mean_s": _f(float(np.mean(s))), "share_below_1": _f(float(np.mean(s < 1))),
                          "min_s": _f(float(np.min(s)))}}


def _normalisations(trades, x_cb, v1, cb, k) -> dict:
    """The CB at other normalisations (reported, not gating): R_ES and the other ratios at each k, and the
    scale-free ES5/sd, MDD/sd and worst/sd of each book."""
    tw = math.fsum(t["minutes"] + 1 for t in trades)
    tw_cb = math.fsum((t["minutes"] + 1) * x for t, x in zip(trades, x_cb))
    m1, m0 = metrics(v1["pnl"]), metrics(cb["pnl"])
    ks = {"EQUAL_AVERAGE_ENTRY_GROSS": k, "K_CAL": CONSTANTS["k_cal"],
          "TIME_WEIGHTED": tw / tw_cb if tw_cb else None,
          "EQUAL_VOL": m1["sd"] / m0["sd"] if m1["sd"] and m0["sd"] else None, "UNNORMALISED": 1.0}
    out = {}
    for name, kk in ks.items():
        if kk is None:
            out[name] = {"k": None}
            continue
        mc = metrics(kk * cb["pnl"])
        out[name] = {"k": kk, "ratios": ratios(m1, mc), "mean": {"v1": m1["mean"], "cb": mc["mean"]}}
    out["scale_free"] = {name: {key: _ratio(m[key], m["sd"]) for key in ("es5", "mdd", "worst")}
                         for name, m in (("v1", m1), ("cb", m0))}
    return out


def _concentration(trades, x_cb, v1, cb, k, cost) -> dict:
    out = {}
    for name, b, x in (("v1", v1, [1.0] * len(trades)), ("cb", cb, x_cb)):
        p = b["pnl"]
        total = float(p.sum())
        per = defaultdict(list)
        for t, xi in zip(trades, x):
            per[t["ticker"]].append(xi * (t["gross_bps"] - 2 * cost))
        stocks = sorted((math.fsum(v) for v in per.values()), reverse=True)
        top = sorted(per, key=lambda t: (-math.fsum(per[t]), t))[:STRESS["concentration"]["top_stocks"]]
        out[name] = {"best_10_days_share": _ratio(float(np.sort(p)[::-1][:STRESS["concentration"]["best_days"]].sum()),
                                                  total),
                     "top_3_stocks_share": _ratio(math.fsum(stocks[:STRESS["concentration"]["top_stocks"]]),
                                                  math.fsum(stocks)),
                     "top_3_stocks": top}
    out["correlation"] = _f(float(np.corrcoef(v1["pnl"], cb["pnl"])[0, 1])) if v1["pnl"].size > 1 else None
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


def checked_before(log: list[dict]) -> bool:
    """The once-only key. Only this check writes this log, so any check entry in it means the check has run, whatever
    its id, wording or digest; an entry of any kind that carries this spec's id or one of its digests counts too."""
    keys = {spec_sha(), params_sha()}
    return any(e.get("split") == "check" or e.get("spec_id") == SPEC_ID or e.get("book_id") == BOOK_ID
               or e.get("spec_sha256") in keys or e.get("params_sha256") in keys for e in log)


def preflight(registration: dict | None, lab_log: list[dict], overlays_log: list[dict], log: list[dict], runner_path,
              runner_sha: str | None = None) -> tuple[dict | None, str]:
    """Everything that must hold before a check bar is read. Returns (v1's check entry, "OK") or (None, the reason).
    runner_sha: the runner's sha256 when it started (the runner passes its own), checked against the file on disk."""
    if registration is None:
        return None, "NOT_REGISTERED"
    if module_sha() != MODULE_SHA:
        return None, "MODULE_CHANGED_SINCE_IMPORT"
    if registration.get("module_sha256") != MODULE_SHA:
        return None, "MODULE_NOT_REGISTERED"
    runner_now = file_sha(runner_path)
    if runner_sha is not None and runner_now != runner_sha:
        return None, "RUNNER_CHANGED_SINCE_IMPORT"
    if registration.get("runner_sha256") != runner_now:
        return None, "RUNNER_NOT_REGISTERED"
    if (registration.get("spec_sha256"), registration.get("params_sha256"), registration.get("sector_map_sha256")) \
            != (spec_sha(), params_sha(), map_sha()):
        return None, "SPECS_NOT_REGISTERED"
    if lab.lab_code_sha() != lab.CODE_SHA:
        return None, "LAB_CODE_CHANGED_SINCE_IMPORT"
    if registration.get("lab_code_sha256") != lab.CODE_SHA:
        return None, "LAB_CODE_NOT_REGISTERED"
    if ov.module_sha() != ov.MODULE_SHA or \
            (registration.get("protected_files") or {}).get(OVERLAYS_MODULE) != ov.MODULE_SHA:
        return None, "OVERLAYS_CODE_NOT_REGISTERED"
    entry, why = ov.v1_check_entry(lab_log)
    if entry is None:
        return None, why
    if entry.get("lab_code_sha256") != lab.CODE_SHA:
        return None, "LAB_CODE_IS_NOT_V1_CHECK_CODE"
    if list(entry.get("months", [])) != list(CHECK):
        return None, "V1_CHECK_MONTHS_DIFFER"
    if not ov.checked_before(overlays_log):
        return None, "OVERLAYS_NOT_YET_CHECKED"
    if checked_before(log):
        return None, "ALREADY_CHECKED"
    return entry, "OK"


def run_check(*, dev_root, prices_dir, tickers: list[str], registration_path=None, lab_log_path=None,
              overlays_log_path=None, log_path=None, runner_path=None, runner_sha: str | None = None) -> dict:
    """The one check of EXT-FADE-CB.v1 on 2020-01..2023-12. Refused unless everything registered holds; the log entry
    is written under a lock before this returns. No bar month after 2023-12 is opened; the daily rows reach the lab's
    replay and the stock-day pass uncut, as in v1's check (see the module's docstring)."""
    log_path = Path(log_path or LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path.with_name(log_path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)             # one run at a time reads, checks and appends the log
        return _run_locked(dev_root=dev_root, prices_dir=prices_dir, tickers=sorted(tickers),
                           registration_path=Path(registration_path or REGISTRATION_PATH),
                           lab_log_path=Path(lab_log_path or lab.LOG_PATH),
                           overlays_log_path=Path(overlays_log_path or ov.LOG_PATH), log_path=log_path,
                           runner_path=Path(runner_path or RUNNER_PATH), runner_sha=runner_sha)


def _refused(reason: str, **extra) -> dict:
    return {"status": "REFUSED", "book_id": BOOK_ID, "reason": reason, **extra}


def _run_locked(*, dev_root, prices_dir, tickers, registration_path, lab_log_path, overlays_log_path, log_path,
                runner_path, runner_sha):
    registration = json.loads(registration_path.read_text()) if registration_path.exists() else None
    log = read_log(log_path)
    entry, why = preflight(registration, lab.read_log(lab_log_path), ov.read_log(overlays_log_path), log, runner_path,
                           runner_sha)
    if entry is None:
        return _refused(why)
    unmapped = sorted(set(tickers) - set(SECTOR_MAP))
    if unmapped:
        return _refused("UNMAPPED_TICKER", tickers=unmapped)
    if lab.inputs_sha(tickers) != entry.get("inputs_sha256"):
        return _refused("INPUTS_ARE_NOT_V1_CHECK_INPUTS")
    if ir.missing_months(dev_root, tickers + ["SPY"], [ir.previous_month(CHECK[0])] + ir.months_between(*CHECK)):
        return _refused("CHECK_FETCH_INCOMPLETE")
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
            raise ValueError("EXT_FADE_PORTFOLIO_NEVER_READS_THE_HOLDOUT")
        return ir.load_month(dev_root, ticker, month)
    # v1's check replay, with the lab's own inputs (the call lab.run makes: the daily rows uncut), unlogged
    run = lab.replay(bars_for, prices, CHECK, tickers, {BASE: BASE_SPEC}, split="check")
    v1 = [t for t in run["trades"] if t["variant"] == BASE]
    if not ov.v1_reproduced(v1, entry):
        return _refused("V1_CHECK_NOT_REPRODUCED", trades=len(v1), logged_trades=entry["trades"])
    days = stock_days(bars_for, prices, CHECK, tickers)
    trades, why = reconcile(run, v1, days)
    if trades is None:
        return _refused(why)
    sessions = sorted(run["eligible"])
    result, why = evaluate(trades, days["candidates"], sessions, YEARS)
    if result is None:
        return _refused(why)
    log_entry = make_log_entry(result, log, entry=entry, registration=registration, bars_sha=bars_sha,
                               prices_sha=prices_sha, tickers=tickers)
    write_log(log + [log_entry], log_path)
    return {"status": "OK", "book_id": BOOK_ID, "spec_id": SPEC_ID, "split": "check", "months": list(CHECK),
            "generated_at": log_entry["at"],
            "v1_check": {k: entry.get(k) for k in ("entry", "trades", "net_bps_at_10", "verdict")},
            "result": result, "log_entry": log_entry, "reading": log_entry["reading"],
            "inputs": {"bars_sha256": bars_sha, "daily_prices_sha256": prices_sha, "lab_code_sha256": lab.CODE_SHA,
                       "module_sha256": MODULE_SHA, "runner_sha256": registration["runner_sha256"],
                       "overlays_module_sha256": ov.MODULE_SHA, "inputs_sha256": lab.inputs_sha(tickers)},
            "use": "INFORMATION_ONLY"}


def reading(verdict: str, v1_verdict: str | None) -> str:
    if v1_verdict != PASS_BAR["counts_only_if_v1_check"]:
        return "v1 did not pass its check: this result is recorded and does not go forward"
    if verdict == "PASS":
        return "counts: enters the 2024+ hold-out as its own pre-registered secondary hypothesis to v1"
    return "fails: final for these data; v1 stays on its plain equal-notional book"


def make_log_entry(result: dict, log: list[dict], *, entry: dict, registration: dict, bars_sha: str, prices_sha: str,
                   tickers: list[str]) -> dict:
    c = result["comparison"]
    return {"entry": len(log) + 1, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "book_id": BOOK_ID, "spec_id": SPEC_ID, "split": "check", "months": list(CHECK),
            "spec_sha256": spec_sha(), "params_sha256": params_sha(), "sector_map_sha256": map_sha(),
            "module_sha256": MODULE_SHA, "runner_sha256": registration["runner_sha256"],
            "lab_code_sha256": lab.CODE_SHA, "overlays_module_sha256": ov.MODULE_SHA,
            "v1_check_entry": entry.get("entry"), "v1_check_verdict": entry.get("verdict"),
            "daily_prices_sha256": prices_sha, "bars_sha256": bars_sha, "inputs_sha256": lab.inputs_sha(tickers),
            "trades": result["trades"], "sessions": result["sessions"], "candidates": result["candidates"]["count"],
            "late_excluded": result["candidates"]["late_excluded"], "k": result["k"],
            "v1": c["v1"], "cb": c["cb"], "ratios": c["ratios"], "bootstrap": c.get("bootstrap"),
            "gates": result["gates"], "verdict": result["verdict"],
            "reading": reading(result["verdict"], entry.get("verdict"))}


# ---- the summary ---------------------------------------------------------------------------------------------------

def _n(x, fmt="+.1f") -> str:
    return "–" if x is None else format(x, fmt)


def _yes(x: bool) -> str:
    return "yes" if x else "no"


def render(report: dict) -> str:
    """The check's summary in plain terms."""
    r = report["result"]
    c, g = r["comparison"], r["gates"]
    v1, cb, ra, b = c["v1"], c["cb"], c["ratios"], c.get("bootstrap", {})
    lines = [f"# EXT-FADE-CB.v1 (constrained book): the one check, {report['months'][0]}..{report['months'][1]}", "",
             f"`{BOOK_ID}`, generated {report['generated_at']}. EXT-FADE.v1's {r['trades']:,} check trades (lab log "
             f"entry {report['v1_check']['entry']}, verdict {report['v1_check']['verdict']}) on the constrained book "
             "frozen on 2026-10-24, against v1's equal-notional book at equal average gross. The verdict against the "
             "pass bar fixed beforehand is final. Log: `docs/evidence/ext-fade-portfolio-log.json`.", "",
             f"**Verdict: {r['verdict']}**. {report['reading']}.", "",
             "| Gate | What | Value | Pass |", "|---|---|---|---|",
             f"| G1 | k at most 2.5 | k {_n(g['G1']['k'], '.4f')} | {_yes(g['G1']['pass'])} |",
             f"| G2 | R_ES below 1 and its UB95 below 1 | R_ES {_n(g['G2']['r_es'], '.4f')}, UB95 "
             f"{_n(g['G2']['ub95'], '.4f')} | {_yes(g['G2']['pass'])} |",
             f"| G3 | W10, DD and MDD all better | W10 {_yes(g['G3']['w10'])}, DD {_yes(g['G3']['dd'])}, MDD "
             f"{_yes(g['G3']['mdd'])} | {_yes(g['G3']['pass'])} |",
             f"| G4 | LB95 of dSR above -0.30 | dSR {_n(g['G4']['dsr'], '+.4f')}, LB95 {_n(g['G4']['lb95'], '+.4f')} | "
             f"{_yes(g['G4']['pass'])} |", "",
             f"Daily P&L in bps of one base notional, at 5 bps a side on stock and 0.5 on SPY, {r['sessions']:,} "
             f"sessions ({r['active_sessions']:,} with a trade):", "",
             "| | Sharpe | Mean/day | sd/day | ES5 | W10 | Worst day | DD | MDD |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, m in (("v1", v1), ("CB at equal gross", cb)):
        lines.append(f"| {name} | {_n(m['sharpe'], '.2f')} | {_n(m['mean'])} | {_n(m['sd'])} | {_n(m['es5'])} | "
                     f"{_n(m['w10'])} | {_n(m['worst'])} | {_n(m['dd'])} | {_n(m['mdd'])} |")
    ci = b.get("ci90", {})
    lines += ["", f"90% intervals (paired block bootstrap, {b.get('reps', 0):,} replicates): R_ES "
              f"{ci.get('r_es')}, dSR {ci.get('dsr')}, W10 ratio {ci.get('w10')}, DD ratio {ci.get('dd')}, MDD "
              f"ratio {ci.get('mdd')}.", ""]
    cand, h = r["candidates"], r["hedge"]
    lines += [f"- **Candidates:** {cand['count']:,} at 09:31 ({cand['kinds']}), {cand['end_as_trades']:,} of them v1's "
              f"trades; {cand['unscorable']} unscorable; late-excluded {cand['late_excluded']} "
              f"({', '.join(cand['late_excluded_sessions']) or 'none'}).",
              f"- **Hedge:** netted SPY cost per trade-unit {_n(h['spy_cost_per_unit']['v1'], '.3f')} bps (v1), "
              f"{_n(h['spy_cost_per_unit']['cb'], '.3f')} (CB), against {_n(h['unnetted_per_unit']['v1'], '.3f')} "
              f"unnetted; {h['same_bar_entry_and_exit']} entries and exits in the same bar."]
    if "binding" in r:
        bd = r["binding"]
        lines.append(f"- **Binding:** cluster on {bd['sessions_binding']['cluster']} sessions, group on "
                     f"{bd['sessions_binding']['group']}, book on {bd['sessions_binding']['book']} (of "
                     f"{bd['candidate_sessions']} with a candidate); mean s {_n(bd['positions']['mean_s'], '.3f')}, "
                     f"{_n(100 * bd['positions']['share_below_1'], '.1f')}% of positions below 1, minimum "
                     f"{_n(bd['positions']['min_s'], '.3f')}.")
    if "stress" in r:
        st = r["stress"]
        lines += ["", "## Reported, not gating", "", "| Version | k | R_ES | UB95 | dSR | LB95 | Gates |",
                  "|---|---|---|---|---|---|---|"]
        rows = [(name.replace("_", " "), x) for name, x in st["costs"].items()]
        rows.append(("post-skip candidates", st["post_skip_candidates"]))
        rows += [(f"without {y}", x) for y, x in st["leave_one_year_out"].items()]
        for name, x in rows:
            gg = x["gates"]
            lines.append(f"| {name} | {_n(x['k'], '.4f')} | {_n(x['ratios'].get('r_es'), '.4f')} | "
                         f"{_n(x['ub95_r_es'], '.4f')} | {_n(x['ratios'].get('dsr'), '+.4f')} | "
                         f"{_n(x['lb95_dsr'], '+.4f')} | "
                         + "".join("1" if gg[k]["pass"] else "0" for k in ("G1", "G2", "G3", "G4")) + " |")
        lines += ["", "| Normalisation | k | R_ES | W10 | DD | MDD |", "|---|---|---|---|---|---|"]
        for name, x in r["normalisations"].items():
            if name == "scale_free" or x.get("k") is None:
                continue
            q = x["ratios"]
            lines.append(f"| {name.replace('_', ' ').lower()} | {_n(x['k'], '.4f')} | {_n(q['r_es'], '.4f')} | "
                         f"{_n(q['w10'], '.3f')} | {_n(q['dd'], '.3f')} | {_n(q['mdd'], '.3f')} |")
        lines += ["", "| Year (at the global k) | Sharpe v1 / CB | ES5 v1 / CB | MDD v1 / CB | Capped sessions |",
                  "|---|---|---|---|---|"]
        for y, x in st["by_year"].items():
            lines.append(f"| {y} | {_n(x['v1']['sharpe'], '.2f')} / {_n(x['cb']['sharpe'], '.2f')} | "
                         f"{_n(x['v1']['es5'], '.0f')} / {_n(x['cb']['es5'], '.0f')} | {_n(x['v1']['mdd'], '.0f')} / "
                         f"{_n(x['cb']['mdd'], '.0f')} | {x['capped_sessions']} |")
        if "intraday_trough" in st:
            it, gr, co = st["intraday_trough"], st["gross"], st["concentration"]
            lines += ["", f"- **Intraday trough:** ES5 {_n(it['es5']['v1'], '.0f')} (v1) vs "
                      f"{_n(it['es5']['cb'], '.0f')} (CB); worst {_n(it['worst']['v1'], '.0f')} vs "
                      f"{_n(it['worst']['cb'], '.0f')}.",
                      f"- **Gross:** peak {_n(gr['peak']['v1'], '.1f')} vs {_n(gr['peak']['cb'], '.1f')} units "
                      f"(normalised); p99 of active sessions {_n(gr['p99_of_active_sessions']['v1'], '.1f')} vs "
                      f"{_n(gr['p99_of_active_sessions']['cb'], '.1f')}.",
                      f"- **Concentration:** best 10 days {_n(co['v1']['best_10_days_share'], '.2f')} (v1) vs "
                      f"{_n(co['cb']['best_10_days_share'], '.2f')} (CB) of P&L; top 3 stocks "
                      f"{_n(co['v1']['top_3_stocks_share'], '.2f')} vs {_n(co['cb']['top_3_stocks_share'], '.2f')}; "
                      f"correlation of the daily series {_n(co['correlation'], '.3f')}."]
    lines += ["", SPEC["use"]["pass"], "", SPEC["use"]["fail"]]
    return "\n".join(lines) + "\n"
