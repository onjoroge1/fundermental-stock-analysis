"""EXT-FADE-CB.v1 (EXT-FADE.v1 on a constrained book): the nested caps, the hedge netting, the daily book, the risk
metrics, the paired bootstrap, the pass bar, the 09:31 candidate set and every refusal of the one-time runner, on
synthetic data. One test reproduces the frozen 2016-2019 calibration record on the pinned data and is skipped when
that data is absent."""

import copy
import functools
import hashlib
import importlib.util
import inspect
import itertools
import json
import math
import os
import pickle
import random
import re
import statistics
import subprocess
from pathlib import Path

import numpy as np
import pytest

from stock_machine.agents.contracts import AGENT_UNIVERSE, digest
from stock_machine.backtest import ext_fade_overlays as ov
from stock_machine.backtest import ext_fade_portfolio as cb
from stock_machine.backtest import implied_vol_replay as ivr
from stock_machine.backtest import intraday_lab as lab
from stock_machine.backtest import intraday_replay as ir

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = json.loads(cb.REGISTRATION_PATH.read_text())
SEMI = ["ADI", "AMAT", "AMD", "AVGO", "INTC", "KLAC", "LRCX", "MU", "NVDA", "QCOM", "TXN"]


def cand(ticker, session="2021-03-10", side=-1, kind=cb.TRADE, late=None):
    return {"ticker": ticker, "session": session, "side": side, "kind": kind, "late": late, "scorable": True}


# ---- the sector map ------------------------------------------------------------------------------------------------

def test_the_map_covers_the_universe_with_13_frozen_industry_groups():
    assert set(cb.SECTOR_MAP) == set(AGENT_UNIVERSE) and len(cb.SECTOR_MAP) == 54
    assert len(set(cb.SECTOR_MAP.values())) == 13
    groups = {g: sorted(t for t, x in cb.SECTOR_MAP.items() if x == g) for g in set(cb.SECTOR_MAP.values())}
    assert groups["SEMI"] == SEMI and groups["HCES"] == ["HIMS"] and groups["DIVFIN"] == ["SOFI"]
    assert groups["MEDIA"] == ["CMCSA", "DIS", "GOOGL", "META", "NFLX"]       # the 2018-09-28 structure
    assert groups["RETAIL"] == ["ABNB", "AMZN", "BKNG", "HD", "LOW", "TGT"]   # the 2023 change is not applied
    assert cb.SECTOR_MAP["UBER"] == "TRANSPORT" and cb.SECTOR_MAP["COST"] == cb.SECTOR_MAP["WMT"] == "STAPLES"


# ---- sizing: the nested proportional scale-down --------------------------------------------------------------------

def test_a_crowded_cluster_is_scaled_to_four_units():
    s, d = cb.scale_down([cand(t) for t in SEMI[:6]])
    assert all(v == pytest.approx(4 / 6) for v in s.values()) and math.fsum(s.values()) == pytest.approx(4.0)
    day = d["2021-03-10"]
    assert day["cluster_binds"] and not day["group_binds"] and not day["book_binds"]
    assert day["removed"]["cluster"] == pytest.approx(2.0) and day["removed"]["book"] == 0


def test_both_sides_of_a_group_share_its_budget():
    cs = [cand(t, side=-1) for t in SEMI[:4]] + [cand(t, side=1) for t in SEMI[4:8]]
    s, d = cb.scale_down(cs)
    assert all(v == pytest.approx(2 / 3) for v in s.values()) and math.fsum(s.values()) == pytest.approx(16 / 3)
    assert d["2021-03-10"]["group_binds"] and not d["2021-03-10"]["cluster_binds"]


def test_the_book_cap_shrinks_every_position_by_the_same_factor():
    names = SEMI[:4] + ["ADBE", "CRM", "IBM", "INTU"] + ["AAPL", "CSCO", "DELL", "HPQ"] + \
        ["CMCSA", "DIS", "GOOGL", "META"] + ["ABNB", "AMZN", "BKNG", "HD"]
    s, d = cb.scale_down([cand(t) for t in names])
    assert all(v == pytest.approx(0.8) for v in s.values()) and math.fsum(s.values()) == pytest.approx(16.0)
    assert d["2021-03-10"]["book_binds"] and d["2021-03-10"]["d"] == pytest.approx(0.8)


def test_the_layers_nest_cluster_then_group_then_book():
    cs = [cand(t, side=-1) for t in SEMI[:8]] + [cand(t, side=1) for t in SEMI[8:]] + \
        [cand(t) for t in ("ADBE", "CRM", "IBM", "INTU", "MSFT", "NOW", "ORCL", "PLTR")] + \
        [cand(t) for t in ("AAPL", "CSCO", "DELL", "HPQ")] + \
        [cand(t) for t in ("CMCSA", "DIS", "GOOGL", "META", "NFLX")] + \
        [cand(t) for t in ("ABNB", "AMZN", "BKNG", "HD", "LOW", "TGT")]
    s, _ = cb.scale_down(cs)
    b_semi = (16 / 3) / (4 + 3)                                    # SEMI: 8 shorts at 0.5 and 3 longs at 1
    total = 16 / 3 + 4 * 4                                         # four other groups at 4 each
    d = 16 / total
    assert s[("ADI", "2021-03-10")] == pytest.approx(0.5 * b_semi * d)
    assert s[("QCOM", "2021-03-10")] == pytest.approx(1.0 * b_semi * d)
    assert s[("ADBE", "2021-03-10")] == pytest.approx(0.5 * d) and s[("AAPL", "2021-03-10")] == pytest.approx(d)
    assert s[("NFLX", "2021-03-10")] == pytest.approx(0.8 * d) and s[("TGT", "2021-03-10")] == pytest.approx(4 / 6 * d)
    assert math.fsum(s.values()) == pytest.approx(16.0)


def test_the_nested_caps_hold_on_random_books():
    rng = random.Random(7)
    names = sorted(cb.SECTOR_MAP)
    for trial in range(300):
        cs = []
        for k in range(rng.randint(1, 4)):
            session = f"2022-01-{k + 3:02d}"
            for t in rng.sample(names, rng.randint(1, 54)):
                cs.append(cand(t, session, side=rng.choice((-1, 1))))
        s, diag = cb.scale_down(cs)
        assert set(s) == {(c["ticker"], c["session"]) for c in cs}
        for session in {c["session"] for c in cs}:
            mine = [c for c in cs if c["session"] == session]
            clusters, groups = {}, {}
            for c in mine:
                x = s[(c["ticker"], session)]
                assert 0 < x <= 1
                key = (cb.SECTOR_MAP[c["ticker"]], c["side"])
                clusters.setdefault(key, []).append(x)
                groups[key[0]] = groups.get(key[0], 0.0) + x
            assert all(len(set(xs)) == 1 for xs in clusters.values())          # one factor per cluster
            assert all(math.fsum(xs) <= 4 + 1e-9 for xs in clusters.values())
            assert all(v <= 16 / 3 + 1e-9 for v in groups.values())
            total = math.fsum(s[(c["ticker"], session)] for c in mine)
            assert total <= 16 + 1e-9
            if diag[session]["book_binds"]:
                assert total == pytest.approx(16.0)                             # a binding layer is tight
            uncapped = all(len(xs) <= 4 for xs in clusters.values()) and \
                all(sum(1 for c in mine if cb.SECTOR_MAP[c["ticker"]] == g) <= 16 / 3 for g in groups) and \
                len(mine) <= 16
            if uncapped:
                assert all(s[(c["ticker"], session)] == 1.0 for c in mine)


def test_without_caps_every_position_is_one_unit():
    names = sorted(cb.SECTOR_MAP)
    s, _ = cb.scale_down([cand(t) for t in names], K=math.inf, S=math.inf, G=math.inf)
    assert set(s.values()) == {1.0}
    with pytest.raises(ValueError, match="TWO_POSITIONS_IN_ONE_NAME"):
        cb.scale_down([cand("AMD"), cand("AMD", side=1)])


# ---- the book and the SPY hedge ------------------------------------------------------------------------------------

S1, S2, S3 = "2021-03-08", "2021-03-09", "2021-03-10"


def hand_trades():
    """Three trades on S1 (one exits at a target in its own entry bar, the same bar another exits), one on S2."""
    return [{"ticker": "AMD", "session": S1, "side": -1, "beta": 1.2, "entry_bar": 1, "exit_bar": 5,
             "reason": "TARGET", "minutes": 4, "gross_bps": 30.0, "net_bps_at_5": 20.0},
            {"ticker": "MSFT", "session": S1, "side": 1, "beta": 0.8, "entry_bar": 1, "exit_bar": None,
             "reason": "CLOSE", "minutes": 388, "gross_bps": -10.0, "net_bps_at_5": -20.0},
            {"ticker": "AAPL", "session": S1, "side": -1, "beta": 1.0, "entry_bar": 5, "exit_bar": 5,
             "reason": "TARGET", "minutes": 0, "gross_bps": 12.0, "net_bps_at_5": 2.0},
            {"ticker": "NVDA", "session": S2, "side": 1, "beta": 1.5, "entry_bar": 2, "exit_bar": None,
             "reason": "CLOSE", "minutes": 387, "gross_bps": 5.0, "net_bps_at_5": -5.0}]


def test_the_hedge_is_netted_per_event_and_an_entry_and_exit_in_one_bar_are_two_events():
    x = [1.0, 0.5, 1.0, 1.0]
    ev = cb.hedge_events(hand_trades(), x)
    # h_i = -side x beta x x_i: AMD +1.2, MSFT -0.4, AAPL +1.0, NVDA -1.5
    assert ev == pytest.approx({(S1, "ENTRY", 1): 0.8, (S1, "ENTRY", 5): 1.0, (S1, "EXIT", 5): -2.2,
                                (S1, "CLOSE", None): 0.4, (S2, "ENTRY", 2): -1.5, (S2, "CLOSE", None): 1.5})
    # the aggregate position, event by event (entries at a bar's open before the exits during it): the sum of the
    # open trades' hedges at every minute, and flat after the close (no orphan)
    h = 0.0
    path = []
    for key in [(S1, "ENTRY", 1), (S1, "ENTRY", 5), (S1, "EXIT", 5), (S1, "CLOSE", None)]:
        h += ev[key]
        path.append(h)
    assert path == pytest.approx([1.2 - 0.4, 1.2 - 0.4 + 1.0, -0.4, 0.0])


def test_the_daily_book_charges_stock_both_ways_and_the_netted_spy_once():
    b = cb.book(hand_trades(), [1.0, 0.5, 1.0, 1.0], [S1, S2, S3], cost_bps=5, spy_cost_bps=0.5)
    assert b["spy_traded"] == pytest.approx([0.8 + 1.0 + 2.2 + 0.4, 3.0, 0.0])
    assert b["spy_unnetted"] == pytest.approx([2 * (1.2 + 0.4 + 1.0), 3.0, 0.0])
    assert b["pnl"] == pytest.approx([(30 - 10) + 0.5 * (-10 - 10) + (12 - 10) - 0.5 * 4.4, (5 - 10) - 0.5 * 3.0, 0.0])
    assert b["gross"] == pytest.approx([2.5, 1.0, 0.0])                    # the hedge is not gross
    free = cb.book(hand_trades(), [1.0] * 4, [S1, S2, S3], cost_bps=5, spy_cost_bps=0.0)
    assert math.fsum(free["pnl"]) == pytest.approx(math.fsum(t["net_bps_at_5"] for t in hand_trades()))


def test_the_intraday_trough_freezes_each_trade_at_its_exit():
    a = np.full(cb.POINTS, np.nan)
    a[1:5] = [-0.002, -0.004, -0.001, 0.0]
    a[5:] = 0.003                                                          # a target hit at bar 5: frozen
    b = np.full(cb.POINTS, np.nan)
    b[3:] = -0.001
    b[200] = -0.006
    trades = [{"session": S1, "marks": a}, {"session": S1, "marks": b},
              {"session": S2, "marks": np.full(cb.POINTS, 0.01)}]
    t = cb.troughs(trades, [1.0, 0.5, 1.0], [S1, S3], 5)
    worst = min((-20 - 10), (-40 - 10), (-10 - 10) + 0.5 * (-10 - 10), (0 - 10) + 0.5 * (-10 - 10),
                (30 - 10) + 0.5 * (-60 - 10))
    assert t[0] == pytest.approx(worst) and t[1] == 0.0                   # S2 is not one of these sessions


# ---- metrics ------------------------------------------------------------------------------------------------------

def test_the_risk_metrics_on_a_hand_series():
    x = [10, -20, 5, -40, 30, -10, 0, 0, 25, -5, 15, -30]
    m = cb.metrics(x)
    assert m["es5"] == 40.0                                                # ceil(0.05 x 12) = 1 day
    assert m["w10"] == pytest.approx(sum(sorted(x)[:10]) / 10) and m["worst"] == -40.0
    assert m["dd"] == pytest.approx(math.sqrt((400 + 1600 + 100 + 25 + 900) / 12))
    assert m["mdd"] == -55.0                                               # from the peak of 10 down to -45
    assert m["sharpe"] == pytest.approx(statistics.mean(x) / statistics.stdev(x) * math.sqrt(252))
    assert cb.metrics([-5, 3, -10])["mdd"] == -12.0                        # C_0 = 0: a loss from the first day counts
    assert cb.metrics(list(range(40)))["es5"] == -statistics.mean([0, 1])  # ceil(0.05 x 40) = 2
    assert cb.metrics([1.0] * 9)["w10"] is None and cb.metrics([1.0] * 9)["sharpe"] is None


# ---- the paired bootstrap ------------------------------------------------------------------------------------------

def test_the_bootstrap_index_is_one_draw_of_circular_monthly_blocks():
    T, reps, block, seed = 50, 7, 21, 20261024
    idx = cb.boot_index(T, reps=reps, block=block, seed=seed)
    starts = np.random.default_rng(seed).integers(0, T, size=(reps, math.ceil(T / block)))
    manual = [list(itertools.chain.from_iterable([(s + j) % T for j in range(block)] for s in row))[:T]
              for row in starts]
    assert idx.shape == (reps, T) and idx.tolist() == manual
    assert cb.boot_index(1006).shape == (10_000, 1006)
    assert (cb.boot_index(1006)[:, :3] == cb.boot_index(1006)[:, :3]).all()       # seeded


def test_both_books_share_one_index_and_k_is_recomputed_in_each_replicate():
    rng = np.random.default_rng(1)
    v = rng.normal(5, 100, 300)
    g = rng.integers(0, 4, 300).astype(float)
    v[g == 0] = 0.0
    same = cb.bootstrap(v, g, v, g)
    assert same["ub95_r_es"] == pytest.approx(1.0) and same["lb95_dsr"] == pytest.approx(0.0, abs=1e-12)
    half = cb.bootstrap(v, g, 0.5 * v, 0.5 * g)                            # k_b = 2 in every replicate
    assert half["ci90"]["k"] == pytest.approx([2.0, 2.0]) and half["ub95_r_es"] == pytest.approx(1.0)
    assert half["ci90"]["mdd"] == pytest.approx([1.0, 1.0]) and half["ci90"]["w10"] == pytest.approx([1.0, 1.0])


def test_an_undefined_replicate_makes_its_bound_undefined():
    v = np.zeros(40)
    v[3] = -5.0
    out = cb.bootstrap(v, np.ones(40), v, np.ones(40))
    assert out["ub95_r_es"] is None and out["undefined"]["r_es"] > 0      # replicates that miss the only loss


# ---- the pass bar ------------------------------------------------------------------------------------------------

def comparison(k=1.05, r_es=0.95, ub=0.99, lb=-0.1, w10=(-100, -90), dd=(50, 45), mdd=(-500, -400)):
    return {"k": k, "ratios": {"r_es": r_es, "dsr": 0.1},
            "v1": {"w10": w10[0], "dd": dd[0], "mdd": mdd[0]}, "cb": {"w10": w10[1], "dd": dd[1], "mdd": mdd[1]},
            "bootstrap": {"ub95_r_es": ub, "lb95_dsr": lb}}


def test_the_pass_bar_needs_every_gate_and_each_edge_is_as_written():
    assert cb.gates(comparison())["verdict"] == "PASS"
    assert cb.gates(comparison(k=2.5))["G1"]["pass"] and not cb.gates(comparison(k=2.5001))["G1"]["pass"]
    assert not cb.gates(comparison(r_es=1.0))["G2"]["pass"] and not cb.gates(comparison(ub=1.0))["G2"]["pass"]
    assert not cb.gates(comparison(ub=None))["G2"]["pass"]
    for kw in ({"w10": (-100, -100)}, {"dd": (50, 50)}, {"mdd": (-500, -500)}, {"w10": (-100, -101)}):
        assert not cb.gates(comparison(**kw))["G3"]["pass"], kw
    assert not cb.gates(comparison(lb=-0.30))["G4"]["pass"] and cb.gates(comparison(lb=-0.2999))["G4"]["pass"]
    assert cb.gates(comparison(lb=-0.31))["verdict"] == "FAIL"
    assert cb.PASS_BAR["pass"] == ["G1", "G2", "G3", "G4"] and cb.PASS_BAR["counts_only_if_v1_check"] == "PASS"


def test_reading_follows_v1s_check():
    assert cb.reading("PASS", "PASS").startswith("counts")
    assert cb.reading("PASS", "FAIL").startswith("v1 did not pass") and cb.reading("FAIL", "FAIL").startswith("v1")
    assert cb.reading("FAIL", "PASS").startswith("fails")


# ---- the 09:31 candidate set on a synthetic world ------------------------------------------------------------------

MONTHS = ("2019-02", "2019-03")
NAMES = ["NVDA", "AMD", "MSFT"]


def candidate_world(daily_errors=True):
    """Three names and SPY with 2% gaps that extend a 1.1% day on alternate sessions, then: NVDA's own daily row
    printed at half its close on 2019-02-04 (DAILY_ROW_ERROR, also for the two sessions after) and 8% off its last
    minute on 2019-02-12 (DAILY_CLOSE_MISMATCH), unless daily_errors is False; AMD's 2019-02-05 short under Rule 201;
    MSFT's 2019-02-19 fill already past its target and no MSFT trade from 09:31 to 09:36 on 2019-03-01; no SPY open
    on 2019-02-26."""
    sessions = ir.fixture_sessions("2018-09-03", "2019-04-30")
    bars, prices = {}, {}
    for k, t in enumerate(NAMES + ["SPY"]):
        rows, close, prev = [], 50.0 + 40 * k, 0.0
        bars[t] = {}
        for i, s in enumerate(sessions):
            gap = (0.02 if prev > 0 else -0.02) if t != "SPY" and (i + k) % 2 == 0 and abs(prev) >= 0.011 else 0.0
            o = round(close * (1 + gap), 2)
            day = ir.synthetic_day(1000 * k + i, o, 0.0)
            bars[t][s] = day
            rows.append({"date": s, "open": o, "high": max(day["h"]), "low": min(day["l"]), "close": day["c"][-1],
                         "adj_close": day["c"][-1], "volume": 1e6})
            prev, close = day["c"][-1] / close - 1, day["c"][-1]
        prices[t] = rows
    at = {s: i for i, s in enumerate(sessions)}
    if daily_errors:
        r = prices["NVDA"][at["2019-02-04"]]
        prices["NVDA"][at["2019-02-04"]] = {**r, "close": round(r["close"] / 2, 2),
                                            "adj_close": round(r["adj_close"] / 2, 2)}
        r = prices["NVDA"][at["2019-02-12"]]
        prices["NVDA"][at["2019-02-12"]] = {**r, "close": round(r["close"] * 0.92, 2),
                                            "adj_close": round(r["adj_close"] * 0.92, 2)}
    i = at["2019-02-04"]
    prices["AMD"][i] = {**prices["AMD"][i], "low": round(prices["AMD"][i - 1]["close"] * 0.85, 2)}
    d = bars["MSFT"]["2019-02-19"]
    d["o"][1] = round(prices["MSFT"][at["2019-02-19"] - 1]["close"] * 1.005, 2)
    d["h"][1] = max(d["h"][1], d["o"][1])
    for key in "ohlcv":
        for b in range(1, 7):
            bars["MSFT"]["2019-03-01"][key][b] = None
    bars["SPY"]["2019-02-26"]["o"][0] = None

    def bars_for(ticker, month):
        return {s: day for s, day in bars[ticker].items() if s[:7] == month}
    return bars_for, prices, bars


@pytest.fixture
def world(monkeypatch):
    bars_for, prices, bars = candidate_world()
    target, real = bars["MSFT"]["2019-03-13"], lab._score_pair

    def score_pair(t, day, *args, **kwargs):                                # the lab cannot score this one
        return None if day is target else real(t, day, *args, **kwargs)
    monkeypatch.setattr(lab, "_score_pair", score_pair)
    run = lab.replay(bars_for, prices, MONTHS, NAMES, {cb.BASE: cb.BASE_SPEC}, split="confirm", events={})
    days = cb.stock_days(bars_for, prices, MONTHS, NAMES)
    return {"run": run, "v1": [t for t in run["trades"] if t["variant"] == cb.BASE], "days": days,
            "bars_for": bars_for, "prices": prices}


def test_the_candidates_are_judged_at_0931_and_late_exclusions_stay_in(world):
    days, run = world["days"], world["run"]
    assert days["eligible"] == run["eligible"]
    odd = {(c["ticker"], c["session"]): (c["kind"], c["late"]) for c in days["candidates"]
           if c["kind"] != cb.TRADE or c["late"] or not c["scorable"]}
    assert odd == {("NVDA", "2019-02-04"): (cb.TRADE, "DAILY_ROW_ERROR"),
                   ("NVDA", "2019-02-06"): (cb.TRADE, "DAILY_ROW_ERROR"),       # its prior move reads the bad row
                   ("NVDA", "2019-02-12"): (cb.TRADE, "DAILY_CLOSE_MISMATCH"),
                   ("AMD", "2019-02-05"): ("RULE_201", None), ("MSFT", "2019-02-19"): ("ENTRY_BEYOND_TARGET", None),
                   ("MSFT", "2019-03-01"): ("NO_ENTRY_MINUTE", None), ("MSFT", "2019-03-13"): (cb.TRADE, None)}
    assert not [c for c in days["candidates"] if c["session"] == "2019-02-26"]  # MISSING_SPY_OPEN: not a candidate
    assert days["skips"] == {k.split(":", 1)[1]: v for k, v in run["skips"].items()} == \
        {"RULE_201": 1, "MISSING_SPY_OPEN": 1, "ENTRY_BEYOND_TARGET": 1, "NO_ENTRY_MINUTE": 1, "UNSCORABLE": 1}
    ends = {(c["ticker"], c["session"]) for c in days["candidates"] if cb.ends_as_trade(c)}
    assert ends == {(t["ticker"], t["session"]) for t in world["v1"]} and len(ends) == len(days["candidates"]) - 7
    with pytest.raises(ValueError, match="HOLDOUT"):
        cb.stock_days(world["bars_for"], world["prices"], ("2023-12", "2024-01"), NAMES)


def _eligibility_inputs(rng):
    """A stock-day for ir.eligibility whose two late checks are clear (no bad or off row, the daily close equal to
    the last minute, no prior session in the history), with a random state of every later check."""
    session, daily_open = "2021-03-10", 50.0
    f = {"stale": False, "bad_row": False, "off_row": False, "prior_date": "2021-03-09", "daily_close": 51.0,
         "daily_open": daily_open, "prior_close_raw": rng.choice([ir.PRICE_FLOOR - 0.01, ir.PRICE_FLOOR, 50.0])}
    o0 = rng.choice([None, daily_open, daily_open * (1 + ir.OPEN_MATCH / 2), daily_open * (1 + 2 * ir.OPEN_MATCH)])
    day = {"o": [] if rng.random() < 0.1 else [o0, 50.2], "c": [50.5, 51.0]}
    hist = ir.History()
    for _ in range(rng.choice([0, ir.NORM_MIN - 1, ir.NORM_MIN])):
        hist.ranges.append([None])
    spy_day = rng.choice([None, {"o": [400.0]}])
    spy_f = rng.choice([None, {"bad_row": False, "off_row": False}, {"bad_row": True, "off_row": False},
                        {"bad_row": False, "off_row": True}])
    return session, day, f, hist, spy_day, spy_f, rng.choice([(), (session,), ("2021-03-11",)])


def test_the_later_checks_mirror_the_labs_eligibility():
    """later_exclusion is ir.eligibility's own sequence after its two late checks: the same reason on every state,
    the same order, and the lab never reaches these checks on a late-flagged stock-day."""
    rng = random.Random(5)
    seen = set()
    for _ in range(3000):
        session, day, f, hist, spy_day, spy_f, actions = _eligibility_inputs(rng)
        mine = cb.later_exclusion(session, day, f, hist, spy_day, spy_f, actions)
        assert mine == ir.eligibility("AMD", session, day, f, hist, spy_day, spy_f, actions), (f, day, actions)
        seen.add(mine)
        assert ir.eligibility("AMD", session, day, {**f, "bad_row": True}, hist, spy_day, spy_f, actions) == \
            "DAILY_ROW_ERROR"                                              # whatever the later checks say
        if mine != "CORPORATE_ACTION":                                     # the one later check the lab makes first
            assert ir.eligibility("AMD", session, day, {**f, "off_row": True}, hist, spy_day, spy_f, actions) == \
                "DAILY_CLOSE_MISMATCH"
    assert seen == set(cb.LATER) | {None}
    order = re.findall(r'return "([A-Z_]+)"', inspect.getsource(ir.eligibility))
    after = order[order.index(cb.LATE[0]) + 1:]
    assert cb.LATE[1] in after and [r for r in after if r not in cb.LATE] == list(cb.LATER)
    assert re.findall(r'return "([A-Z_]+)"', inspect.getsource(cb.later_exclusion)) == list(cb.LATER)


def _membership(days):
    return {(c["ticker"], c["session"]): c["late"] for c in days["candidates"]}


def test_a_late_flag_never_changes_whether_a_stock_day_is_a_candidate(monkeypatch):
    """Condition 2: a stock-day is a candidate exactly when it would be one without its own late checks. With and
    without NVDA's two bad daily rows, the candidates are the same on every session whose features do not read those
    rows; on the bad rows' own sessions only the flag differs. A check known before the open (a corporate action, an
    official open the 09:30 bar contradicts) drops a late-flagged stock-day as it drops an eligible one."""
    bad = cb.stock_days(*candidate_world()[:2], MONTHS, NAMES)
    clean = cb.stock_days(*candidate_world(daily_errors=False)[:2], MONTHS, NAMES)
    reads_a_bad_row = {"2019-02-05", "2019-02-06", "2019-02-13", "2019-02-14"}   # their prior close or prior move

    def kept(days):
        return {k: v for k, v in _membership(days).items() if not (k[0] == "NVDA" and k[1] in reads_a_bad_row)}
    assert set(kept(bad)) == set(kept(clean)) and not any(kept(clean).values())
    assert {k: v for k, v in kept(bad).items() if v} == {("NVDA", "2019-02-04"): "DAILY_ROW_ERROR",
                                                         ("NVDA", "2019-02-12"): "DAILY_CLOSE_MISMATCH"}
    every = _membership(bad)
    clean_trade = min(s for (t, s), late in kept(bad).items() if t == "NVDA" and late is None and s > "2019-02-04")
    with monkeypatch.context() as m:                                       # corporate actions: on the bad-row session
        m.setitem(ir.CORPORATE_ACTIONS, "NVDA", ("2019-02-04", clean_trade))           # and on a clean v1 trade
        acted = _membership(cb.stock_days(*candidate_world()[:2], MONTHS, NAMES))
    assert acted == {k: v for k, v in every.items() if k not in {("NVDA", "2019-02-04"), ("NVDA", clean_trade)}}
    bars_for, prices, _ = candidate_world()
    i = next(j for j, r in enumerate(prices["NVDA"]) if r["date"] == "2019-02-04")
    prices["NVDA"][i] = {**prices["NVDA"][i], "open": round(prices["NVDA"][i]["open"] * 1.01, 2)}   # PRICE_MISMATCH
    assert _membership(cb.stock_days(bars_for, prices, MONTHS, NAMES)) == \
        {k: v for k, v in every.items() if k != ("NVDA", "2019-02-04")}


def test_late_skipped_and_unscorable_candidates_use_their_share_of_the_budget(world):
    one = {t: "X" for t in NAMES}
    s, _ = cb.scale_down(world["days"]["candidates"], K=1, sector_map=one)
    # each of these sessions: a trade and, the same way, a candidate that never trades
    assert s[("MSFT", "2019-02-12")] == 0.5                                 # NVDA late-excluded
    assert s[("NVDA", "2019-02-19")] == 0.5                                 # MSFT past its target at 09:31
    assert s[("NVDA", "2019-03-01")] == 0.5                                 # MSFT had no entry minute
    assert s[("NVDA", "2019-03-13")] == 0.5                                 # MSFT unscorable
    assert s[("NVDA", "2019-02-25")] == s[("MSFT", "2019-02-25")] == 1.0    # opposite sides: two clusters


def test_every_trade_reconciles_with_the_lab_and_the_book_with_its_nets(world):
    trades, why = cb.reconcile(world["run"], world["v1"], world["days"])
    assert why == "OK" and len(trades) == len(world["v1"])
    for t, rec in zip(trades, world["v1"]):
        assert (t["ticker"], t["session"], t["entry_bar"], t["reason"]) == \
            (rec["ticker"], rec["session"], rec["entry_bar"], rec["reason"])
        assert t["residual_diff_bps"] <= 1e-6 and t["side"] == (-1 if rec["gap"] > 0 else 1)
        assert t["exit_bar"] == (rec["entry_bar"] + rec["minutes"] if rec["reason"] == "TARGET" else None)
        assert t["marks"][cb.POINTS - 1] * 1e4 == pytest.approx(rec["gross_bps"])
        assert np.isnan(t["marks"][:rec["entry_bar"]]).all()


def test_the_cb_with_no_caps_is_v1(world, monkeypatch):
    trades, _ = cb.reconcile(world["run"], world["v1"], world["days"])
    sessions = sorted(world["run"]["eligible"])
    monkeypatch.setitem(cb.CONSTANTS, "reps", 300)
    for key in ("K", "S", "G"):
        monkeypatch.setitem(cb.CONSTANTS, key, math.inf)
    result, why = cb.evaluate(trades, world["days"]["candidates"], sessions, ("2019",))
    c = result["comparison"]
    assert why == "OK" and result["k"] == 1.0 and c["v1"] == c["cb"]
    assert c["ratios"]["r_es"] == 1.0 and c["ratios"]["dsr"] == 0.0
    assert result["binding"]["positions"] == {"mean_s": 1.0, "share_below_1": 0.0, "min_s": 1.0}
    assert result["verdict"] == "FAIL" and not result["gates"]["G2"]["pass"]       # no better: not a pass
    assert result["reconciliation"]["v1_total_c_spy_0"] == \
        pytest.approx(math.fsum(t["net_bps"][5] for t in world["v1"]))


def test_a_binding_cap_changes_only_the_sizes(world, monkeypatch):
    trades, _ = cb.reconcile(world["run"], world["v1"], world["days"])
    sessions = sorted(world["run"]["eligible"])
    monkeypatch.setitem(cb.CONSTANTS, "reps", 300)
    monkeypatch.setitem(cb.CONSTANTS, "K", 1)
    monkeypatch.setattr(cb, "SECTOR_MAP", {**cb.SECTOR_MAP, "MSFT": "SEMI"})
    result, _ = cb.evaluate(trades, world["days"]["candidates"], sessions, ("2019",))
    assert result["trades"] == len(world["v1"]) and result["binding"]["sessions_binding"]["cluster"] > 0
    assert result["binding"]["positions"]["min_s"] == 0.5 and result["k"] > 1
    assert result["comparison"]["cb"] != result["comparison"]["v1"]
    assert result["candidates"]["late_excluded"] == 3 and result["candidates"]["unscorable"] == 1
    assert set(result["stress"]["costs"]) == {"stock_10_bps", "stock_15_bps", "spy_0_bps", "spy_1_bps"}
    json.dumps(result)                                                     # plain numbers: no arrays, no bars


def test_an_unmapped_candidate_is_refused(world, monkeypatch):
    monkeypatch.setattr(cb, "SECTOR_MAP", {k: v for k, v in cb.SECTOR_MAP.items() if k != "MSFT"})
    assert cb.reconcile(world["run"], world["v1"], world["days"]) == (None, "UNMAPPED_TICKER")


def test_the_book_must_reconcile_with_the_labs_nets(world):
    trades, _ = cb.reconcile(world["run"], world["v1"], world["days"])
    trades[0]["net_bps_at_5"] += 1e-3
    assert cb.evaluate(trades, world["days"]["candidates"], sorted(world["run"]["eligible"]), ("2019",)) == \
        (None, "V1_BOOK_DISAGREES_WITH_THE_LAB")


# ---- the one-time runner -------------------------------------------------------------------------------------------

CHECK_NAMES = ["AMD", "NVDA"]


def synthetic_check():
    """Two names and SPY over the check months (and the lab's warm-up and detection months), with 2% gaps that extend
    a 1.2% day often enough for v1 to trade; plus the hold-out's first daily row, which only the lab's printing-error
    test may read."""
    sessions = ir.fixture_sessions("2019-08-01", "2023-12-31")
    bars, prices = {}, {}
    for k, t in enumerate(CHECK_NAMES + ["SPY"]):
        rows, close, prev = [], 50.0 + 150 * k, 0.0
        bars[t] = {}
        for i, s in enumerate(sessions):
            gap = (0.02 if prev > 0 else -0.02) if t != "SPY" and (i + k) % 3 == 0 and abs(prev) >= 0.012 else 0.0
            o = round(close * (1 + gap), 2)
            day = ir.synthetic_day(1000 * k + i, o, 0.0)
            bars[t][s] = day
            rows.append({"date": s, "open": o, "high": max(day["h"]), "low": min(day["l"]), "close": day["c"][-1],
                         "adj_close": day["c"][-1], "volume": 1e6})
            prev, close = day["c"][-1] / close - 1, day["c"][-1]
        rows.append({**rows[-1], "date": "2024-01-02"})
        prices[t] = rows
    return sessions, bars, prices


@functools.lru_cache(maxsize=1)
def _synthetic_check_bytes() -> bytes:
    """The world, and v1's trade count and mean net at 10 bps on it (the lab's own replay), built once."""
    sessions, bars, prices = synthetic_check()
    run = lab.replay(lambda t, m: {s: d for s, d in bars[t].items() if s[:7] == m}, prices, cb.CHECK, CHECK_NAMES,
                     {cb.BASE: cb.BASE_SPEC}, split="check", events={})
    v1 = (len(run["trades"]), lab._mean([t["net_bps"][10] for t in run["trades"]]))
    return pickle.dumps((sessions, bars, prices, v1))


def registration_of() -> dict:
    return {"module_sha256": cb.module_sha(), "runner_sha256": cb.file_sha(cb.RUNNER_PATH),
            "spec_sha256": cb.spec_sha(), "params_sha256": cb.params_sha(), "sector_map_sha256": cb.map_sha(),
            "lab_code_sha256": lab.CODE_SHA, "protected_files": {cb.OVERLAYS_MODULE: ov.MODULE_SHA}}


@pytest.fixture
def check_world(tmp_path, monkeypatch):
    sessions, bars, prices, (n_v1, mean_v1) = pickle.loads(_synthetic_check_bytes())   # a fresh copy per test

    def load_month(root, ticker, month):
        assert month <= "2023-12", "read a hold-out month"
        return {s: d for s, d in bars[ticker].items() if s[:7] == month}
    monkeypatch.setattr(ir, "load_month", load_month)
    monkeypatch.setattr(ir, "missing_months", lambda root, tickers, months: [])
    monkeypatch.setattr(ir, "files_sha", lambda root, months=None: "b" * 64)
    monkeypatch.setitem(cb.CONSTANTS, "reps", 200)                          # a quick bootstrap (registered below)
    prices_dir = tmp_path / "prices"                  # real files: the lab's code hash covers load_prices' source
    prices_dir.mkdir()
    for t, rows in prices.items():
        (prices_dir / f"{t}.json").write_text(json.dumps({"ticker": t, "rows": rows}))
    monkeypatch.setattr(ir, "DAILY_PRICES_SHA256", ivr.load_prices(prices_dir)[1])
    check = {"entry": 21, "split": "check", "months": list(cb.CHECK), "variant": cb.BASE, "spec": cb.BASE_SPEC,
             "spec_sha256": lab.spec_sha(cb.BASE_SPEC), "lab_code_sha256": lab.CODE_SHA, "bars_sha256": "b" * 64,
             "inputs_sha256": lab.inputs_sha(CHECK_NAMES), "trades": n_v1, "net_bps_at_10": mean_v1,
             "verdict": "PASS"}
    confirm = {**check, "entry": 17, "split": "confirm", "months": list(lab.CONFIRM)}
    overlays = [{"entry": i + 1, "split": "check", "overlay_id": ov.OVERLAY_ID, "spec_id": sid,
                 "spec_sha256": ov.spec_sha(sid), "params_sha256": ov.params_sha(sid), "verdict": "FAIL"}
                for i, sid in enumerate((ov.SW, ov.P2, ov.IVR))]
    w = {"tmp": tmp_path, "prices_dir": prices_dir, "check": check, "confirm": confirm, "lab_extra": [],
         "overlays": overlays, "earlier": [], "registration": registration_of(), "log": tmp_path / "cb-log.json",
         "trades": n_v1, "tickers": list(CHECK_NAMES)}

    def write():
        reg = tmp_path / "registration.json"
        if w["registration"] is None:
            reg.unlink(missing_ok=True)
        else:
            reg.write_text(json.dumps(w["registration"]))
        (tmp_path / "lab-log.json").write_text(json.dumps([w["confirm"], w["check"], *w["lab_extra"]]))
        (tmp_path / "overlays-log.json").write_text(json.dumps(w["overlays"]))
        if w["earlier"]:
            cb.write_log(w["earlier"], w["log"])

    def run_it(**kw):
        write()
        return cb.run_check(dev_root=tmp_path, prices_dir=prices_dir, tickers=w["tickers"],
                            registration_path=tmp_path / "registration.json", lab_log_path=tmp_path / "lab-log.json",
                            overlays_log_path=tmp_path / "overlays-log.json", log_path=w["log"], **kw)
    w["run"] = run_it
    return w


def test_the_one_check_runs_logs_once_and_never_runs_twice(check_world):
    w = check_world
    out = w["run"](runner_sha=cb.file_sha(cb.RUNNER_PATH))
    assert out["status"] == "OK", out
    log = cb.read_log(w["log"])
    assert len(log) == 1 and log[0]["entry"] == 1 and log[0]["split"] == "check" and log[0]["spec_id"] == cb.SPEC_ID
    e = log[0]
    assert (e["spec_sha256"], e["params_sha256"], e["sector_map_sha256"]) == (cb.spec_sha(), cb.params_sha(),
                                                                              cb.map_sha())
    assert e["module_sha256"] == cb.MODULE_SHA and e["lab_code_sha256"] == lab.CODE_SHA and e["v1_check_entry"] == 21
    assert e["overlays_module_sha256"] == ov.MODULE_SHA and e["verdict"] in ("PASS", "FAIL")
    assert e["trades"] == w["trades"] == out["result"]["trades"] and e["reading"] == out["reading"]
    assert out["result"]["candidates"]["end_as_trades"] == w["trades"] > 100
    assert set(out["result"]["stress"]["leave_one_year_out"]) == set(cb.YEARS)
    text = cb.render(out)
    assert "the one check" in text and "G2" in text and "Reported, not gating" in text
    dumped = json.dumps(out)                                                # aggregates only: plain numbers
    assert "marks" not in dumped and '"day"' not in dumped
    again = w["run"]()
    assert again == {"status": "REFUSED", "book_id": cb.BOOK_ID, "reason": "ALREADY_CHECKED"}
    assert cb.read_log(w["log"]) == log                                     # a refusal is never logged


def _set(path, value):
    """A case that sets w[path[0]][path[1]]... to value (None deletes the key)."""
    def setup(w, mp):
        target = w
        for key in path[:-1]:
            target = target[key]
        if value is None:
            del target[path[-1]]
        else:
            target[path[-1]] = value
    return setup


def _edited_runner(w, mp):
    other = w["tmp"] / "runner.py"
    other.write_text(cb.RUNNER_PATH.read_text() + "\n# edited\n")
    return {"runner_path": other}


def _other_daily_files(w, mp):
    path = w["prices_dir"] / "AMD.json"
    path.write_text(json.dumps(json.loads(path.read_text()), indent=1))     # the same rows, other bytes


def _altered_days(edit):
    def setup(w, mp):
        real = cb.stock_days

        def altered(*args, **kwargs):
            days = real(*args, **kwargs)
            edit(days)
            return days
        mp.setattr(cb, "stock_days", altered)
    return setup


def _first_trade(days):
    return next(c for c in days["candidates"] if cb.ends_as_trade(c))


def _drop_a_trade(days):
    days["candidates"].remove(_first_trade(days))


def _shift_an_entry(days):
    c = _first_trade(days)
    c["trade"] = {**c["trade"], "entry_bar": c["trade"]["entry_bar"] + 1}


def _another_beta(days):
    c = _first_trade(days)
    c["f"] = {**c["f"], "beta": c["f"]["beta"] + 1e-3}


def _book_off_by_a_little(w, mp):
    real = cb.reconcile

    def altered(*args):
        trades, why = real(*args)
        trades[0]["net_bps_at_5"] += 1e-3
        return trades, why
    mp.setattr(cb, "reconcile", altered)


ZERO = "0" * 64
OTHER = {**cb.BASE_SPEC, "gap": 0.02}
REFUSALS = {
    "no registration": ("NOT_REGISTERED", lambda w, mp: w.update(registration=None)),
    "module edited since import": ("MODULE_CHANGED_SINCE_IMPORT", lambda w, mp: mp.setattr(cb, "MODULE_SHA", ZERO)),
    "module not registered": ("MODULE_NOT_REGISTERED", _set(("registration", "module_sha256"), ZERO)),
    "runner edited since it started": ("RUNNER_CHANGED_SINCE_IMPORT", lambda w, mp: {"runner_sha": ZERO}),
    "runner not registered": ("RUNNER_NOT_REGISTERED", _set(("registration", "runner_sha256"), ZERO)),
    "another runner file": ("RUNNER_NOT_REGISTERED", _edited_runner),
    "the spec digest not registered": ("SPECS_NOT_REGISTERED", _set(("registration", "spec_sha256"), ZERO)),
    "the parameter digest not registered": ("SPECS_NOT_REGISTERED", _set(("registration", "params_sha256"), ZERO)),
    "the map digest not registered": ("SPECS_NOT_REGISTERED", _set(("registration", "sector_map_sha256"), ZERO)),
    "a constant changed after registration": ("SPECS_NOT_REGISTERED",
                                              lambda w, mp: mp.setitem(cb.CONSTANTS, "K", 5)),
    "lab code edited since import": ("LAB_CODE_CHANGED_SINCE_IMPORT",
                                     lambda w, mp: mp.setattr(lab, "CODE_SHA", ZERO)),
    "lab code not registered": ("LAB_CODE_NOT_REGISTERED", _set(("registration", "lab_code_sha256"), ZERO)),
    "the overlays' code not registered": ("OVERLAYS_CODE_NOT_REGISTERED",
                                          _set(("registration", "protected_files", cb.OVERLAYS_MODULE), ZERO)),
    "the overlays' code edited since import": ("OVERLAYS_CODE_NOT_REGISTERED",
                                               lambda w, mp: mp.setattr(ov, "MODULE_SHA", ZERO)),
    "no check of v1 in the lab log": ("NO_V1_CHECK_IN_THE_LAB_LOG", _set(("check", "split"), "explore")),
    "a check of another strategy": ("NO_V1_CHECK_IN_THE_LAB_LOG",
                                    lambda w, mp: w["check"].update(spec=OTHER, spec_sha256=lab.spec_sha(OTHER))),
    "v1 checked twice": ("V1_CHECKED_MORE_THAN_ONCE", lambda w, mp: w["lab_extra"].append({**w["check"], "entry": 22})),
    "v1 checked with other lab code": ("LAB_CODE_IS_NOT_V1_CHECK_CODE", _set(("check", "lab_code_sha256"), ZERO)),
    "v1 checked on other months": ("V1_CHECK_MONTHS_DIFFER", _set(("check", "months"), ["2020-01", "2022-12"])),
    "the overlays not yet checked": ("OVERLAYS_NOT_YET_CHECKED", _set(("overlays",), [])),
    "only a note in the overlays' log": ("OVERLAYS_NOT_YET_CHECKED",
                                         _set(("overlays",), [{"entry": 1, "split": "note", "spec_sha256": ZERO}])),
    "any earlier check in this log": ("ALREADY_CHECKED", _set(("earlier",), [
        {"entry": 1, "split": "check", "spec_sha256": ZERO}])),
    "an earlier entry with this spec's id": ("ALREADY_CHECKED", _set(("earlier",), [
        {"entry": 1, "split": "note", "spec_id": cb.SPEC_ID}])),
    "an earlier entry with the spec digest": ("ALREADY_CHECKED", lambda w, mp: w["earlier"].append(
        {"entry": 1, "split": "note", "spec_sha256": cb.spec_sha()})),             # the digests as registered
    "an earlier entry with the parameter digest": ("ALREADY_CHECKED", lambda w, mp: w["earlier"].append(
        {"entry": 1, "split": "note", "params_sha256": cb.params_sha()})),
    "a ticker the map lacks": ("UNMAPPED_TICKER", _set(("tickers",), CHECK_NAMES + ["ZZZZ"])),
    "other tickers than v1's check": ("INPUTS_ARE_NOT_V1_CHECK_INPUTS",
                                      _set(("check", "inputs_sha256"), lab.inputs_sha(["AMD", "NVDA", "MSFT"]))),
    "a check month not fetched": ("CHECK_FETCH_INCOMPLETE",
                                  lambda w, mp: mp.setattr(ir, "missing_months", lambda root, t, m: ["2021-06"])),
    "other daily price files": ("DAILY_PRICES_NOT_REGISTERED", _other_daily_files),
    "the replay's behaviour changed": ("REPLAY_CODE_CHANGED",
                                       lambda w, mp: mp.setattr(ir, "behavior_digest", lambda: ZERO)),
    "other bars than v1's check": ("BARS_ARE_NOT_V1_CHECK_BARS", _set(("check", "bars_sha256"), "c" * 64)),
    "v1 logged one trade more": ("V1_CHECK_NOT_REPRODUCED",
                                 lambda w, mp: w["check"].update(trades=w["check"]["trades"] + 1)),
    "v1's logged mean differs by 1e-9": ("V1_CHECK_NOT_REPRODUCED", lambda w, mp: w["check"].update(
        net_bps_at_10=w["check"]["net_bps_at_10"] + 1e-9)),
    "eligibility differs from the lab's": ("ELIGIBILITY_DISAGREES_WITH_THE_LAB", _altered_days(
        lambda d: d["eligible"].__setitem__(min(d["eligible"]), d["eligible"][min(d["eligible"])] + 1))),
    "a lab trade missing from the candidates": ("CANDIDATES_DISAGREE_WITH_THE_LAB", _altered_days(_drop_a_trade)),
    "a candidate entering a minute late": ("CANDIDATES_DISAGREE_WITH_THE_LAB", _altered_days(_shift_an_entry)),
    "skips counted differently": ("CANDIDATES_DISAGREE_WITH_THE_LAB",
                                  _altered_days(lambda d: d["skips"].__setitem__("RULE_201", 99))),
    "a trade's beta differs": ("BOOK_TRADE_RECONCILIATION", _altered_days(_another_beta)),
    "the book off the lab's nets": ("V1_BOOK_DISAGREES_WITH_THE_LAB", _book_off_by_a_little),
}


@pytest.mark.parametrize("case", list(REFUSALS))
def test_the_runner_refuses_and_logs_nothing(check_world, monkeypatch, case):
    w = check_world
    reason, setup = REFUSALS[case]
    kwargs = setup(w, monkeypatch) or {}
    earlier = copy.deepcopy(w["earlier"])
    out = w["run"](**kwargs)
    assert out["status"] == "REFUSED" and out["reason"] == reason, out
    if earlier:
        assert cb.read_log(w["log"]) == earlier                             # the earlier log is left as it was
    else:
        assert not w["log"].exists()


def test_every_refusal_the_runner_implements_has_a_case():
    source = Path(cb.__file__).read_text()
    reasons = set(re.findall(r'_refused\("([A-Z0-9_]+)"', source)) | set(re.findall(r'return None, "([A-Z0-9_]+)"',
                                                                                     source))
    reasons |= {"NO_V1_CHECK_IN_THE_LAB_LOG", "V1_CHECKED_MORE_THAN_ONCE"}   # ext_fade_overlays.v1_check_entry's
    assert reasons and reasons == {reason for reason, _ in REFUSALS.values()}


def test_the_check_opens_no_2024_bars_and_gives_the_lab_and_the_stock_day_pass_the_daily_rows_uncut(check_world,
                                                                                                    monkeypatch):
    """As registered: the lab's replay and the stock-day pass get the daily rows uncut, as lab.run gave them to v1's
    check (its printing-error test reads the next open). The fixture's bar loader fails on any month after 2023-12."""
    w = check_world
    seen = {}

    def spy(name, real):
        def call(*args, **kwargs):
            seen.setdefault(name, set()).add(max(r["date"] for rows in args[1].values() for r in rows))
            return real(*args, **kwargs)
        return call
    monkeypatch.setattr(lab, "replay", spy("replay", lab.replay))
    monkeypatch.setattr(cb, "stock_days", spy("stock_days", cb.stock_days))
    assert w["run"]()["status"] == "OK"
    assert seen == {"replay": {"2024-01-02"}, "stock_days": {"2024-01-02"}}


# ---- what is frozen ----------------------------------------------------------------------------------------------

def _reworded():
    spec = copy.deepcopy(cb.SPEC)
    spec["what"] += " "
    return spec


def test_the_spec_digest_covers_the_wording_and_the_parameter_digest_does_not(monkeypatch):
    spec_before, before, map_before = cb.spec_sha(), cb.params_sha(), cb.map_sha()
    assert before == digest({"constants": cb.CONSTANTS, "sector_map": cb.SECTOR_MAP, "pass_bar": cb.PASS_BAR})
    with monkeypatch.context() as m:
        m.setattr(cb, "SPEC", _reworded())
        assert cb.spec_sha() != spec_before and cb.params_sha() == before and cb.map_sha() == map_before
    for key, value in (("K", 5), ("ni_margin", 0.25), ("seed", 1), ("k_cal", 1.0)):
        with monkeypatch.context() as m:
            m.setitem(cb.CONSTANTS, key, value)
            assert cb.params_sha() != before, key
    with monkeypatch.context() as m:
        m.setitem(cb.SECTOR_MAP, "UBER", "SOFT")
        assert cb.params_sha() != before and cb.map_sha() != map_before
    with monkeypatch.context() as m:
        m.setitem(cb.PASS_BAR["G4"], "lb95_dsr_above", -0.25)
        assert cb.params_sha() != before


def test_checked_before_is_the_once_only_key():
    assert cb.checked_before([]) is False and cb.checked_before([{"split": "note", "spec_sha256": ZERO}]) is False
    assert cb.checked_before([{"split": "check"}]) and cb.checked_before([{"book_id": cb.BOOK_ID}])
    assert cb.checked_before([{"split": "note", "params_sha256": cb.params_sha()}])


def test_the_protected_files_and_the_lab_code_are_unchanged():
    assert lab.CODE_SHA.startswith("518b5557") and lab.lab_code_sha() == lab.CODE_SHA
    assert ir.behavior_digest() == ir.BEHAVIOR_SHA256
    confirm = [e for e in lab.read_log() if e["split"] == "confirm" and e["variant"] == cb.BASE]
    assert len(confirm) == 1 and confirm[0]["lab_code_sha256"] == lab.CODE_SHA and confirm[0]["verdict"] == "PASS"
    overlays = json.loads((ROOT / cb.OVERLAYS_REGISTRATION).read_text())
    assert ov.MODULE_SHA == ov.module_sha() == overlays["module_sha256"]
    assert ov.file_sha(ov.RUNNER_PATH) == overlays["runner_sha256"]
    assert set(cb.PROTECTED) == set(ov.PROTECTED) | {cb.OVERLAYS_MODULE, cb.OVERLAYS_RUNNER}
    for path in cb.PROTECTED:
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == REGISTRATION["protected_files"][path], path
    for path in ov.PROTECTED:
        assert REGISTRATION["protected_files"][path] == overlays["protected_files"][path], path


def test_the_protected_files_are_byte_identical_to_origin_main():
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if git("rev-parse", "--verify", "--quiet", "origin/main").returncode != 0:
        pytest.skip("no origin/main in this checkout (a shallow clone); the pinned hashes still apply")
    for path in cb.PROTECTED:
        main = git("show", f"origin/main:{path}")
        assert main.returncode == 0 and (ROOT / path).read_bytes() == main.stdout, path


def test_the_registration_matches_the_code():
    reg = REGISTRATION
    assert reg["module_sha256"] == cb.MODULE_SHA == cb.module_sha()
    assert reg["runner_sha256"] == cb.file_sha(cb.RUNNER_PATH)
    assert reg["calibration_script_sha256"] == cb.file_sha(ROOT / reg["calibration_script"])
    assert reg["module_sha256"] not in Path(cb.__file__).read_text()        # the module never holds its own hash
    assert (reg["spec_sha256"], reg["params_sha256"], reg["sector_map_sha256"]) == \
        (cb.spec_sha(), cb.params_sha(), cb.map_sha())
    assert reg["spec"] == json.loads(json.dumps(cb.SPEC))
    assert reg["params"] == json.loads(json.dumps(cb.params_of()))
    assert reg["sector_map"] == cb.SECTOR_MAP and reg["pass_bar"] == json.loads(json.dumps(cb.PASS_BAR))
    assert reg["constants"] == json.loads(json.dumps(cb.CONSTANTS)) and reg["stress"] == cb.STRESS
    assert reg["lab_code_sha256"] == lab.CODE_SHA and reg["daily_prices_sha256"] == ir.DAILY_PRICES_SHA256
    assert reg["overlays_registration"]["sha256"] == cb.file_sha(ROOT / cb.OVERLAYS_REGISTRATION)
    assert reg["spec_id"] == cb.SPEC_ID and reg["registration"] == cb.BOOK_ID
    assert reg["variants"]["this_freeze"] == 1 and reg["variants"]["total"] == 1 + 1413 + 16
    assert lab.variants_tried(lab.read_log()) >= reg["variants"]["lab_log"] == 16
    assert reg["log"] == "docs/evidence/ext-fade-portfolio-log.json" == str(cb.LOG_PATH.relative_to(ROOT))
    assert [step["runner"] for step in reg["run_order"]] == ["scripts/run_intraday_lab.py",
                                                            "scripts/run_ext_fade_overlays.py",
                                                            "scripts/run_ext_fade_portfolio.py"]


def _calibration_script():
    spec = importlib.util.spec_from_file_location("calibrate_cb", ROOT / "scripts" / "calibrate_ext_fade_portfolio.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_recorded_calibration_reproduces_the_frozen_record():
    calibrate = _calibration_script()
    got = REGISTRATION["calibration"]
    assert {k: v["expected"] for k, v in got["reproduction"].items()} == {k: e for k, (e, _) in
                                                                          calibrate.EXPECTED.items()}
    assert all(v["match"] and round(v["got"], calibrate.EXPECTED[k][1]) == v["expected"]
               for k, v in got["reproduction"].items())
    assert got["dry_run"] == calibrate.DRY_RUN and got["last_day_read"] == "2019-12-31"
    assert got["module_sha256"] == cb.module_sha() and got["lab_code_sha256"] == lab.CODE_SHA
    assert (got["spec_sha256"], got["params_sha256"]) == (cb.spec_sha(), cb.params_sha())
    assert calibrate.EXPECTED["k"] == (cb.CONSTANTS["k_cal"], 6)          # k_cal is the calibration's own k


DATA = Path(os.environ.get("EXT_FADE_DATA_ROOT", ROOT / "data"))


@pytest.mark.skipif(not (DATA / "ibkr_intraday" / "v1").is_dir() or not (DATA / "ibkr_prices" / "2026-10-20").is_dir(),
                    reason="the pinned 2016-2019 data is absent")
def test_the_calibration_reproduces_the_frozen_record_on_the_pinned_data():
    """2016-01..2019-12 only: the script's bar loader refuses later months and its daily rows are cut at
    2019-12-31; lab.replay(split='confirm') logs nothing."""
    calibrate = _calibration_script()
    report = calibrate.calibrate(DATA / "ibkr_intraday" / "v1", DATA / "ibkr_prices" / "2026-10-20")
    assert report["daily_prices_sha256"] == ir.DAILY_PRICES_SHA256
    assert {k: v["match"] for k, v in report["reproduction"].items()} == {k: True for k in calibrate.EXPECTED}
    assert report["dry_run_match"] and report["module_sha256"] == cb.MODULE_SHA
