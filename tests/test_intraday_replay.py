"""Intraday first-hour replay: the setups on hand-built bars, fills, scoring and the twin, evaluation, gates."""

import gzip
import json
import math
import sys
from collections import Counter
from pathlib import Path
from statistics import NormalDist

import pytest

from stock_machine.agents.contracts import AGENT_UNIVERSE, digest
from stock_machine.backtest import event_replay as er
from stock_machine.backtest import intraday_replay as ir

ROOT = Path(__file__).resolve().parents[1]
PINNED_PROTOCOL_SHA256 = "fae9981bfbda872c1689983d944eaf9dd8f640d968237f6e88291e0f0f859b04"
PINNED_BEHAVIOR_SHA256 = "ac31e0da135e74b8c84d289fcbafe966d31f160d5c82e01461f25729d50fbb3b"


def day_of(bars, n=60):
    """A day from (o, h, l, c) tuples, padded with flat bars at the last close up to n bars."""
    bars = list(bars)
    while len(bars) < n:
        c = bars[-1][3]
        bars.append((c, c, c, c))
    return {"o": [b[0] for b in bars], "h": [b[1] for b in bars], "l": [b[2] for b in bars],
            "c": [b[3] for b in bars], "v": [1000.0] * len(bars)}


def feat(pc=100.0, **kw):
    out = {"prior_close_raw": pc, "prior_close": pc, "dividend_anomaly": False, "prior_low": pc * 0.99,
           "close_before_prior": pc, "daily_open": pc, "daily_close": pc, "beta": 1.0, "atr14": 2.0,
           "sigma_d": 0.02, "history": 300, "overnight": 0.0, "overnight_spy": 0.0, "overnight_sd": 0.01}
    out.update(kw)
    return out


def flat_spy(n=390, price=400.0):
    return {k: [price] * n for k in "ohlc"} | {"v": [1.0] * n}


# ---- G1 ------------------------------------------------------------------------

G1_DOWN = [(96.0, 96.2, 95.5, 95.6),     # 09:30, gap down 4%
           (95.6, 96.4, 95.5, 96.3),     # the counter-move starts (closes against the gap)
           (96.3, 96.8, 96.1, 96.6),
           (96.6, 96.7, 96.0, 96.05),    # no new high, closes below the previous low: the failure
           (96.0, 96.1, 95.0, 95.2)]


def test_g1_shorts_the_failed_counter_move_with_its_high_as_stop_and_a_1r_target():
    day = day_of(G1_DOWN)
    t = ir.g1(day, feat(), 96.0 / 100 - 1)
    assert t["strategy"] == "G1" and t["side"] == -1 and t["entry_bar"] == 4 and t["entry"] == 96.0
    assert t["stop"] == 96.8 and t["target"] == pytest.approx(95.2) and t["risk"] == pytest.approx(0.8)
    out = ir.score(t, day, flat_spy(), 400.0, 1.0, 0.01)
    assert out["reason"] == "TARGET" and out["exit"] == pytest.approx(95.2) and out["r_gross"] == pytest.approx(1.0)


def test_g1_skips_a_counter_move_that_retraced_half_the_gap_and_small_gaps():
    retraced = list(G1_DOWN)
    retraced[2] = (96.3, 98.1, 96.1, 96.6)      # 2.1 of a 4.0 gap
    assert ir.g1(day_of(retraced), feat(), -0.04) is None
    assert ir.g1(day_of(G1_DOWN), feat(), -0.029) is None


def test_g1_short_is_skipped_under_the_short_sale_rule():
    assert ir.g1(day_of(G1_DOWN), feat(prior_low=89.0, close_before_prior=100.0), -0.04) == {"skip": "RULE_201"}
    assert ir.ssr_active(feat(), day_of([(91.9, 92, 91, 91.5)]), 0)                 # open 8% below
    assert ir.ssr_active(feat(), day_of([(95, 95, 89.9, 90.5)]), 0)                 # traded 10% below
    assert not ir.ssr_active(feat(), day_of([(95, 95, 90.1, 90.5), (90, 90, 89, 89)]), 0)   # later bar not seen


# ---- G2 ------------------------------------------------------------------------

def g2_day(fill_low=99.9):
    up = [(102.0 + 0.1 * i, 102.1 + 0.1 * i, 101.8 + 0.1 * i, 102.1 + 0.1 * i) for i in range(5)]   # F0: 102 -> 102.5
    rev = [(102.5, 102.7, 102.3, 102.4), (102.4, 102.5, 102.1, 102.2), (102.2, 102.3, 101.9, 102.0),
           (102.0, 102.1, 101.7, 101.8), (101.8, 101.9, 101.5, 101.6)]                            # F1 closes 101.6
    after = [(101.6, 101.7, 101.0, 101.1), (101.1, 101.2, fill_low, 100.4)]
    return day_of(up + rev + after)


def test_g2_fades_a_reversal_five_minute_bar_to_the_prior_close():
    day = g2_day()
    t = ir.g2(day, feat(), 0.02)
    assert (t["side"], t["entry_bar"], t["entry"], t["stop"], t["target"]) == (-1, 10, 101.6, 102.7, 100.0)
    out = ir.score(t, day, flat_spy(), 400.0, 1.0, 0.01)
    assert out["reason"] == "TARGET" and out["exit"] == 100.0


def test_g2_needs_a_gap_of_1_to_3_percent_that_has_not_filled_and_1r_to_the_close():
    assert ir.g2(g2_day(), feat(), 0.031) is None and ir.g2(g2_day(), feat(), 0.009) is None
    filled = g2_day()
    filled["l"][3] = 99.9                        # the gap filled inside F0
    assert ir.g2(filled, feat(), 0.02) is None
    assert ir.g2(g2_day(), feat(pc=101.0, prior_close=101.0), 0.02) is None     # 0.6 to target, 1.1 of risk


def test_gap_fill_counts_are_unconditional_rates():
    assert ir.gap_filled(g2_day(), 100.0, 0.02, 11) and not ir.gap_filled(g2_day(), 100.0, 0.02, 10)
    assert not ir.gap_filled(g2_day(fill_low=100.1), 100.0, 0.02, 59)


# ---- B1 ------------------------------------------------------------------------

def b1_day(bar5_open=102.6):
    opening = [(102.5, 103.0, 101.5, 102.8)] + [(102.8, 102.9, 102.0, 102.6)] * 4
    return day_of(opening + [(bar5_open, bar5_open + 0.1, bar5_open - 0.1, bar5_open), (102.6, 102.7, 102.4, 102.5),
                             (102.9, 103.2, 102.8, 103.1)])


def test_b1_buys_the_opening_range_break_in_the_gap_direction_at_the_level():
    t = ir.b1(b1_day(), feat(), 0.025)
    assert (t["side"], t["entry_bar"], t["entry"], t["stop"], t["by_stop"]) == (1, 7, 103.0, 101.5, True)
    out = ir.score(t, b1_day(), flat_spy(), 400.0, 1.0, 0.01)
    assert out["reason"] == "TIME" and out["exit"] == 103.1          # the 10:29 close
    assert ir.b1(b1_day(), feat(), 0.019) is None


def test_a_stop_entry_gapping_through_fills_at_the_open_and_strictly_through_the_level():
    t = ir.b1(b1_day(bar5_open=103.4), feat(), 0.025)
    assert (t["entry_bar"], t["entry"]) == (5, 103.4)
    touch = b1_day()
    touch["h"][7] = 103.0                         # touching is not trading through
    for k in "ohlc":
        touch[k][7:] = [102.9] * (60 - 7)
    touch["h"][7] = 103.0
    assert ir.b1(touch, feat(), 0.025) is None


# ---- C1 ------------------------------------------------------------------------

def c1_day(breakout):
    normal = [(100.0, 100.1, 99.9, 100.0)] * 10            # log range ~0.002 = the norm
    squeeze = [(100.0, 100.055, 99.945, 100.0), (100.0, 100.045, 99.955, 100.0),
               (100.0, 100.035, 99.965, 100.0), (100.0, 100.025, 99.975, 100.0)]
    return day_of(normal + squeeze + [breakout])


NORMS = [math.log(100.1 / 99.9)] * 60


def test_c1_arms_both_ways_after_four_narrowing_bars_and_takes_the_break():
    day = c1_day((100.0, 100.3, 99.98, 100.25))
    t = ir.c1(day, feat(), NORMS)
    assert (t["side"], t["entry_bar"], t["entry"], t["stop"], t["both"]) == (1, 14, 100.055, 99.945, False)
    down = ir.c1(c1_day((100.0, 100.02, 99.7, 99.75)), feat(), NORMS)
    assert (down["side"], down["entry"], down["stop"]) == (-1, 99.945, 100.055)


def test_c1_bar_through_both_levels_fills_the_level_its_path_reaches_first_and_is_stopped_in_it():
    up_bar = c1_day((100.0, 100.3, 99.7, 100.2))                  # open, low, high, close: the sell stop first
    t = ir.c1(up_bar, feat(), NORMS)
    assert t["both"] and (t["side"], t["entry"], t["stop"]) == (-1, 99.945, 100.055)
    out = ir.score(t, up_bar, flat_spy(), 400.0, 1.0, 0.01)
    assert out["reason"] == "STOP" and out["stop_in_fill_bar"] and out["exit"] == 100.055
    down_bar = c1_day((100.0, 100.3, 99.7, 99.8))                 # open, high, low, close: the buy stop first
    assert ir.c1(down_bar, feat(), NORMS)["side"] == 1


def test_c1_under_the_short_sale_rule_keeps_its_buy_stop():
    ssr = feat(prior_low=89.0, close_before_prior=100.0)
    later_up = c1_day((100.0, 100.02, 99.7, 99.75))
    for k, v in zip("ohlc", (99.75, 100.3, 99.74, 100.2)):
        later_up[k][16] = v
    t = ir.c1(later_up, ssr, NORMS)
    assert (t["side"], t["entry_bar"]) == (1, 16)
    sweep = ir.c1(c1_day((100.0, 100.3, 99.7, 100.2)), ssr, NORMS)
    assert sweep["side"] == 1 and sweep["both"]
    assert ir.c1(c1_day((100.0, 100.02, 99.7, 99.75)), ssr, NORMS) == {"skip": "RULE_201"}


def test_c1_needs_strict_narrowing_below_the_threshold_and_resolvable_ranges():
    loose = c1_day((100.0, 100.3, 99.98, 100.25))
    loose["h"][10], loose["l"][10] = 100.07, 99.93                 # 0.70 of normal
    assert ir.c1(loose, feat(), NORMS) is None
    assert ir.c1(c1_day((100.0, 100.3, 99.98, 100.25)), feat(), None) is None
    cheap = [x / 10000 for x in NORMS]                              # a normal range under 5 cents
    assert ir.c1(c1_day((100.0, 100.3, 99.98, 100.25)), feat(), cheap) is None


# ---- X1, E1, A1 --------------------------------------------------------------

def test_x1_follows_the_first_candle_with_a_tenth_of_atr_stop_to_the_close():
    opening = [(100.0, 100.6, 99.8, 100.2)] + [(100.2, 100.4, 100.0, 100.3)] * 3 + [(100.3, 100.5, 100.1, 100.4)]
    day = day_of(opening + [(100.4, 100.7, 100.45, 100.65)], n=390)
    t = ir.x1_candidate(day, feat(atr14=2.0, daily_close=101.0))
    assert (t["side"], t["entry_bar"], t["entry"], t["stop"], t["close"]) == (1, 5, 100.6, pytest.approx(100.4), 101.0)
    out = ir.score(t, day, flat_spy(), 400.0, 1.0, 0.01)
    assert out["reason"] == "CLOSE" and out["exit"] == 101.0
    gap_up_bar = day_of(opening + [(100.0, 100.75, 99.98, 100.7)], n=390)          # opens below the stop
    t = ir.x1_candidate(gap_up_bar, feat(atr14=2.0, daily_close=101.5))
    assert ir.score(t, gap_up_bar, flat_spy(), 400.0, 1.0, 0.01)["reason"] == "CLOSE"
    worst = ir.score(t, gap_up_bar, flat_spy(), 400.0, 1.0, 0.01, fill_bar="worst")
    assert worst["reason"] == "STOP" and worst["stop_in_fill_bar"]
    falls_back = day_of(opening + [(100.0, 100.75, 99.98, 100.3)], n=390)           # closes back below the stop
    t = ir.x1_candidate(falls_back, feat(atr14=2.0, daily_close=101.5))
    assert ir.score(t, falls_back, flat_spy(), 400.0, 1.0, 0.01)["reason"] == "STOP"
    doji = day_of([(100.0, 100.6, 99.8, 100.2)] + [(100.2, 100.4, 99.9, 100.0)] * 4, n=390)
    assert ir.x1_candidate(doji, feat()) is None


def test_e1_needs_a_pre_open_or_after_close_release_and_a_one_sigma_residual_by_ten():
    day = day_of([(103.0, 103.2, 102.8, 103.0)] * 31, n=390)
    spy = flat_spy()
    t = ir.e1(day, feat(), "PRE_OPEN", spy, feat(pc=400.0))
    assert (t["side"], t["entry_bar"], t["close"]) == (1, 30, 100.0)
    assert ir.e1(day, feat(), "OTHER", spy, feat(pc=400.0)) is None
    assert ir.e1(day, feat(sigma_d=0.04), "AFTER_CLOSE", spy, feat(pc=400.0)) is None
    assert ir.release_timing("2024-01-25T21:05:00+00:00", "2024-01-26") == "AFTER_CLOSE"
    assert ir.release_timing("2024-01-26T12:00:00+00:00", "2024-01-26") == "PRE_OPEN"
    assert ir.release_timing("2024-01-26T15:00:00+00:00", "2024-01-26") == "OTHER"


def test_a1_shorts_a_two_sigma_attention_gap_away_from_earnings():
    day = day_of([(104.0, 104.1, 103.9, 104.0)] * 6, n=390)
    f = feat(overnight=0.04, overnight_spy=0.0, overnight_sd=0.01)
    t = ir.a1(day, f, set(), "2024-01-25", "2024-01-26")
    assert (t["side"], t["entry_bar"]) == (-1, 5)
    assert ir.a1(day, f, {"2024-01-25"}, "2024-01-25", "2024-01-26") is None
    assert ir.a1(day, feat(overnight=0.014, overnight_spy=0.0, overnight_sd=0.001), set(), None, "2024-01-26") is None


# ---- fills and scoring ---------------------------------------------------------

def test_a_market_entry_bar_touching_stop_and_target_is_stopped():
    day = day_of([(100, 100, 100, 100), (100, 101.5, 98.5, 100)])
    out = ir.simulate_exit(day, 1, 1, 99.0, 101.0, 59, entry_by_stop=False)
    assert out["reason"] == "STOP" and out["exit"] == 99.0


def test_a_stop_entry_bar_is_checked_for_the_stop_only_and_later_gaps_exit_at_the_open():
    day = day_of([(100, 100, 100, 100), (100, 101.5, 99.5, 101), (98.0, 98.5, 97.5, 98)])
    assert ir.simulate_exit(day, 1, 1, 99.0, 101.0, 59, entry_by_stop=True, entry=100.5) == {
        "exit": 98.0, "exit_bar": 2, "reason": "STOP", "at_open": True}
    assert ir.after_fill(day, 1, 1, 100.5) == [100.5, 101.5, 101]          # up bar: open, low, high, close
    assert ir.after_fill(day, 1, 1, 99.0) == [100, 99.5, 101.5, 101]       # opened through: from the open


def test_missing_minutes_carry_the_position_and_the_time_exit_uses_the_last_close():
    day = day_of([(100, 100, 100, 100)] * 3 + [(101, 101, 101, 101)])
    day["o"][2] = day["h"][2] = day["l"][2] = day["c"][2] = None
    day["c"][59] = None
    out = ir.simulate_exit(day, 1, 1, 90.0, None, 59, entry_by_stop=False)
    assert out == {"exit": 101, "exit_bar": 58, "reason": "TIME", "at_open": False}


def test_the_residual_hedges_beta_spy_over_the_same_minutes_and_the_twin_splits_skill_from_bracket():
    day = day_of([(100.0, 100, 100, 100)] * 31 + [(102.0, 102, 102, 102)], n=390)
    spy = flat_spy()
    for b in range(31, 390):
        spy["o"][b] = spy["c"][b] = 404.0
    t = ir._trade("E1", 1, 30, 100.0, None, None, 389, by_stop=False, close=102.0)
    own = ir.score(t, day, spy, 404.0, 1.5, 0.01)
    assert own["residual"] == pytest.approx(0.02 - 1.5 * 0.01)
    assert own["reward"] == pytest.approx((0.005 - 0.002) / 0.01)
    mirror = ir.score(t, day, spy, 404.0, 1.5, 0.01, side=-1)
    assert (own["reward"] + mirror["reward"]) / 2 == pytest.approx(-0.002 / 0.01)
    assert (own["reward"] - mirror["reward"]) / 2 == pytest.approx(0.005 / 0.01)


def test_the_mirror_reflects_stop_and_target_through_the_entry():
    day = day_of([(96.0, 96.2, 95.5, 95.6), (96.0, 96.3, 95.9, 96.0), (96.0, 96.9, 95.9, 96.85)])
    t = ir._trade("G1", -1, 1, 96.0, 96.8, 95.2, 59, by_stop=False)
    mirror = ir.score(t, day, flat_spy(), 400.0, 1.0, 0.01, side=1)
    assert mirror["reason"] == "TARGET" and mirror["exit"] == pytest.approx(96.8)
    own = ir.score(t, day, flat_spy(), 400.0, 1.0, 0.01)
    assert own["reason"] == "STOP" and own["exit"] == 96.8


def test_spy_is_taken_at_the_open_for_open_fills_and_the_midpoint_inside_a_bar():
    day = b1_day()
    spy = flat_spy()
    spy["o"][7], spy["c"][7] = 400.0, 402.0
    t = ir.b1(day, feat(), 0.025)
    out = ir.score(t, day, spy, 400.0, 1.0, 0.01)
    assert out["residual"] == pytest.approx((103.1 / 103.0 - 1) - (400.0 / 401.0 - 1))


def test_window_variance_and_scale():
    day = day_of([(100, 100.5, 99.5, 100.2), (100.2, 100.4, 100.0, 100.1), (100.1, 100.3, 99.9, 99.8)])
    prefix = ir.day_square_returns(day)
    direct = math.log(100.2 / 100) ** 2 + math.log(100.1 / 100.2) ** 2 + math.log(99.8 / 100.1) ** 2
    assert ir.window_var(day, prefix, 0, 2) == pytest.approx(direct)
    assert ir.window_scale(ir.deque([1e-12] * 15), 60) == pytest.approx(0.05 * math.sqrt(60 / 390 / 252))
    assert ir.window_scale(ir.deque([1e-4] * 14), 60) is None


def test_the_dividend_factor_takes_ex_dates_and_ignores_cent_rounding():
    assert ir.dividend_factor({"close": 50.0, "adj_close": 49.0}, {"close": 49.5, "adj_close": 49.5}) == (
        pytest.approx(0.98), False)
    assert ir.dividend_factor({"close": 50.0, "adj_close": 49.99}, {"close": 50.2, "adj_close": 50.2}) == (1.0, False)
    assert ir.dividend_factor({"close": 50.0, "adj_close": 51.0}, {"close": 50.0, "adj_close": 50.0}) == (1.0, True)


def test_daily_features_use_only_the_past_and_the_dividend_adjusted_prior_close():
    rows = [{"date": f"2020-01-{d:02d}", "open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0,
             "adj_close": 49.0 if d < 5 else 50.0} for d in range(2, 30)]
    feats = ir.daily_features(rows, rows)
    assert feats["2020-01-05"]["prior_close"] == pytest.approx(50.0 * 0.98)
    assert feats["2020-01-06"]["prior_close"] == 50.0 and feats["2020-01-15"]["atr14"] is None
    assert feats["2020-01-17"]["atr14"] == pytest.approx(2.0) and feats["2020-01-17"]["beta"] == 1.0


# ---- the run, evaluation and verdicts -------------------------------------------

def test_the_fixture_exercises_every_strategy_and_the_cross_sections_are_capped():
    run = ir.behavior_run()
    by = Counter(t["strategy"] for t in run["trades"])
    assert set(by) == {*ir.CONFIRMATORY, *ir.EXPLORATORY}
    per_session = Counter((t["session"], t["strategy"]) for t in run["trades"])
    assert max(v for (s, k), v in per_session.items() if k == "X1") <= ir.X1_TOP
    assert max(v for (s, k), v in per_session.items() if k == "M1") == 1
    keys = Counter((t["session"], t["ticker"], t["strategy"]) for t in run["trades"] if t["strategy"] != "R1")
    assert max(keys.values()) == 1                                  # one trade per stock, strategy and session
    assert all(t["session"] >= "2019-02-01" for t in run["trades"])  # the warm-up month only builds history
    assert {"PRICE_MISMATCH", "MISSING_OPEN_BAR", "STALE_PRIOR_CLOSE", "NO_DAILY_BARS", "CORPORATE_ACTION"} \
        <= set(run["excluded"])
    assert run["excluded_by_ticker"]["T4"]["CORPORATE_ACTION"] == 1 and run["excluded_by_ticker"]["T2"]["STALE_PRIOR_CLOSE"]
    assert any(k.endswith("RULE_201") for k in run["skips"]) and run["counts"]["DIVIDEND_ANOMALY"] == 1


def test_later_bars_and_sessions_cannot_change_earlier_trades():
    bars_for, prices, events, tickers, actions = ir.behavior_fixture()
    full = ir.replay(bars_for, prices, events, ("2019-02", "2019-03"), tickers, corporate_actions=actions)

    def february_only(ticker, month):
        return bars_for(ticker, month) if month <= "2019-02" else {}
    cut = ir.replay(february_only, prices, events, ("2019-02", "2019-03"), tickers, corporate_actions=actions)
    key = lambda t: (t["session"], t["ticker"], t["strategy"], t["side"])   # noqa: E731
    early = sorted((t for t in full["trades"] if t["session"] < "2019-03"), key=key)
    assert early == sorted(cut["trades"], key=key)

    def scrambled(ticker, month):                    # every bar after 10:30 changed
        days = bars_for(ticker, month)
        return {s: {k: v[:60] + [x * 1.05 if x else x for x in v[60:]] for k, v in d.items()} for s, d in days.items()}
    later = ir.replay(scrambled, prices, events, ("2019-02", "2019-03"), tickers, corporate_actions=actions)
    first_hour = [t for t in full["trades"] if t["strategy"] in ("G1", "G2", "B1", "C1")]
    assert sorted(first_hour, key=key) == sorted((t for t in later["trades"] if t["strategy"] in ("G1", "G2", "B1", "C1")),
                                                 key=key)


def test_supported_needs_the_mean_its_lower_bound_and_direction_skill_above_zero_with_coverage():
    months = ("2024-01", "2025-12")
    g1 = {k: ir.evaluate(ir.synthetic_book(**kw), months, holdout=True)["G1"] for k, kw in ir.VERDICT_CASES.items()}
    assert {k: v["verdict"] for k, v in g1.items()} == {
        "pass": "SUPPORTED", "no_skill": "NOT_SUPPORTED", "no_mean": "NOT_SUPPORTED",
        "few_stocks": "INSUFFICIENT_COVERAGE", "few_months": "INSUFFICIENT_COVERAGE", "fragile": "NOT_SUPPORTED",
        "cost_robust": "SUPPORTED"}
    assert g1["pass"]["labels"] == {"robustness": "ROBUST", "cost_sensitive": True}
    assert g1["cost_robust"]["labels"]["cost_sensitive"] is False and g1["fragile"]["labels"]["robustness"] == "FRAGILE"
    dev = ir.evaluate(ir.synthetic_book(), months, holdout=False)
    assert "verdict" not in dev["G1"] and dev["G1"]["z"] == ir.Z_95
    report = ir.stage_report(ir.synthetic_book(), "holdout", months)
    assert report["label"] == "INTRADAY_HOLDOUT_PSEUDO_OUT_OF_SAMPLE" and report["strategies"]["G1"]["z"] == ir.Z_FAMILY


def test_the_book_divides_each_session_by_its_eligible_stock_days_and_scores_empty_sessions_zero():
    trades = [{"session": "a", "reward": 2.0}, {"session": "a", "reward": 1.0}, {"session": "c", "reward": 4.0}]
    assert ir.book(trades, ["a", "b", "c"], {"a": 3, "b": 2, "c": 2}, "reward") == [1.0, 0.0, 2.0]
    assert ir.nw_lag(100) == 4 and ir.nw_lag(670) == 6


def test_coverage_caps_any_one_stock_share():
    trades = [{"ticker": "A" if i < 60 else f"T{i % 20}", "month": f"2024-{i % 12 + 1:02d}"} for i in range(200)]
    cov = ir.coverage(trades, ("2024-01", "2026-08"))
    assert cov["max_stock_share"] > ir.MAX_STOCK_SHARE and not cov["met"]


# ---- data files, gates and pins ------------------------------------------------

def write_month(root, ticker, month, sessions, price=100.0, count=1, manifest=True):
    folder = Path(root) / ticker
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["session,minute,open,high,low,close,volume,wap,count"]
    for s in sessions:
        for i in (0, 1, 389):
            hh, mm = divmod(570 + i, 60)
            lines.append(f"{s},{hh:02d}:{mm:02d},{price},{price + 1},{price - 1},{price},10,{price},{count if i else 1}")
    with gzip.open(folder / f"{month}.csv.gz", "wt") as fh:
        fh.write("\n".join(lines) + "\n")
    if manifest:
        (folder / f"{month}.json").write_text("{}")


def test_month_files_load_by_minute_drop_placeholder_bars_and_hash_by_content(tmp_path):
    write_month(tmp_path, "AAA", "2024-01", ["2024-01-02"])
    day = ir.load_month(tmp_path, "AAA", "2024-01")["2024-01-02"]
    assert len(day["c"]) == 390 and day["o"][0] == 100.0 and day["o"][2] is None and day["h"][389] == 101.0
    assert ir.load_month(tmp_path, "AAA", "2024-02") == {}
    write_month(tmp_path, "BBB", "2024-01", ["2024-01-02"], count=0)          # no trades after 09:30
    assert len(ir.load_month(tmp_path, "BBB", "2024-01")["2024-01-02"]["c"]) == 1
    sha = ir.files_sha(tmp_path)
    write_month(tmp_path, "AAA", "2024-01", ["2024-01-02"])                     # same bars, a new gzip header
    assert ir.files_sha(tmp_path) == sha
    write_month(tmp_path, "AAA", "2024-01", ["2024-01-02"], price=100.5)
    assert ir.files_sha(tmp_path) != sha
    assert ir.missing_months(tmp_path, ["AAA", "CCC"], ["2024-01"]) == ["CCC/2024-01"]
    (tmp_path / "AAA" / "2024-01.partial.json").write_text("{}")
    assert ir.missing_months(tmp_path, ["AAA"], ["2024-01"]) == ["AAA/2024-01"]


def test_the_holdout_refuses_until_protocol_code_prices_and_both_complete_inputs_are_registered(tmp_path, monkeypatch):
    dev, hold = tmp_path / "dev", tmp_path / "hold"
    tickers = sorted(AGENT_UNIVERSE)
    write_month(dev, "AAA", "2023-12", ["2023-12-01"])
    write_month(hold, "AAA", "2024-01", ["2024-01-02"])
    good = ir.DAILY_PRICES_SHA256

    def ready():
        return ir.holdout_ready(dev, hold, tickers, good)
    assert ready() == (False, "PROTOCOL_NOT_REGISTERED_FOR_HOLDOUT")
    assert ir.run(dev_root=dev, holdout_root=hold, prices_dir=tmp_path, stage="holdout", tickers=tickers)["status"] == "REFUSED"
    monkeypatch.setattr(ir, "REGISTERED_PROTOCOL_SHA256", "0" * 64)
    assert ready()[1] == "PROTOCOL_CHANGED_SINCE_REGISTRATION"
    monkeypatch.setattr(ir, "REGISTERED_PROTOCOL_SHA256", ir.PROTOCOL_SHA256)
    monkeypatch.setattr(ir, "BEHAVIOR_SHA256", "0" * 64)
    assert ready()[1] == "REPLAY_CODE_CHANGED"
    monkeypatch.setattr(ir, "BEHAVIOR_SHA256", ir.behavior_digest())
    assert ir.holdout_ready(dev, hold, tickers[1:], good)[1] == "UNIVERSE_NOT_REGISTERED"
    assert ir.holdout_ready(dev, hold, tickers, "0" * 64)[1] == "DAILY_PRICES_NOT_REGISTERED"
    assert ready()[1] == "DEVELOPMENT_INPUTS_NOT_REGISTERED"
    monkeypatch.setattr(ir, "REGISTERED_DEV_INPUTS", ir.files_sha(dev))
    assert ready()[1] == "HOLDOUT_INPUTS_NOT_REGISTERED"
    monkeypatch.setattr(ir, "REGISTERED_HOLDOUT_INPUTS", ir.files_sha(hold))
    assert ready()[1] == "HOLDOUT_FETCH_INCOMPLETE"
    for t in [*tickers, "SPY"]:
        write_month(dev, t, "2023-12", ["2023-12-01"])
        for m in ir.months_between(*ir.HOLDOUT):
            write_month(hold, t, m, [f"{m}-02"])
    monkeypatch.setattr(ir, "REGISTERED_DEV_INPUTS", ir.files_sha(dev))
    monkeypatch.setattr(ir, "REGISTERED_HOLDOUT_INPUTS", ir.files_sha(hold))
    assert ready() == (True, "READY")
    write_month(hold, "AAPL", "2024-01", ["2024-01-02"], price=99.0)
    assert ready()[1] == "HOLDOUT_INPUTS_CHANGED"
    with pytest.raises(ValueError):
        ir.run(dev_root=dev, prices_dir=tmp_path, stage="other", tickers=tickers)
    with pytest.raises(ValueError):
        ir.run(dev_root=dev, prices_dir=tmp_path, stage="holdout", tickers=tickers)


def test_development_refuses_unregistered_prices_and_an_incomplete_fetch(tmp_path):
    out = ir.run(dev_root=tmp_path, prices_dir=tmp_path, stage="dev", tickers=["AAPL"])
    assert out["reason"] == "DAILY_PRICES_NOT_REGISTERED"
    prices = ROOT / "data" / "ibkr_prices" / "2026-10-20"
    if not prices.exists():
        pytest.skip("local IBKR daily files absent (they are never committed)")
    out = ir.run(dev_root=tmp_path, prices_dir=prices, stage="dev", tickers=["AAPL"])
    assert out["reason"] == "INPUTS_INCOMPLETE" and out["missing"] == 2 * (len(ir.months_between(*ir.DEV)) + 1)


def test_sessions_without_minute_bars_are_counted_on_spy_s_calendar():
    bars_for, prices, events, tickers, actions = ir.behavior_fixture()

    def without_t3_march(ticker, month):
        return {} if ticker == "T3" and month == "2019-03" else bars_for(ticker, month)
    run = ir.replay(without_t3_march, prices, events, ir.BEHAVIOR_MONTHS, tickers, corporate_actions=actions)
    assert run["excluded_by_ticker"]["T3"]["NO_MINUTE_BARS"] == 21
    assert sum(run["eligible"].values()) + sum(run["excluded"].values()) == len(tickers) * len(run["sessions"])


def test_earnings_come_from_the_pinned_dataset_with_release_timing(tmp_path):
    ev = ir.earnings()
    timings = Counter(t for e in ev.values() for t in e["primary"].values())
    assert set(timings) == {"PRE_OPEN", "AFTER_CLOSE", "OTHER"} and timings["AFTER_CLOSE"] > timings["PRE_OPEN"]
    raw = json.loads((ROOT / er.EARNINGS_ANNOUNCEMENTS_PATH).read_text())
    raw["events"] = raw["events"][:-1]
    altered = tmp_path / "e.json"
    altered.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="EARNINGS_DATA_CHANGED"):
        ir.earnings(altered)


def test_the_protocol_names_every_rule_and_the_family_z_is_bonferroni_over_five():
    assert ir.PROTOCOL_SHA256 == digest(ir.PROTOCOL)
    assert set(ir.PROTOCOL["strategies"]) == set(ir.CONFIRMATORY)
    assert set(ir.EXPLORATORY) <= set(ir.PROTOCOL["exploratory"])
    assert ir.Z_FAMILY == pytest.approx(NormalDist().inv_cdf(1 - 0.05 / (2 * len(ir.CONFIRMATORY))), abs=1e-6)
    assert ir.PROTOCOL["universe"] == sorted(AGENT_UNIVERSE) and ir.PROTOCOL["behavior_sha256"] == ir.BEHAVIOR_SHA256
    assert ir.DEV[1] < ir.HOLDOUT[0] and ir.PROTOCOL["splits"]["holdout"] == list(ir.HOLDOUT)


def test_the_behavior_pin_is_the_same_with_python_3_11_float_addition(monkeypatch):
    """Python 3.12 made the built-in sum of floats compensated; CI runs 3.11 and 3.12. Plain left-to-right addition
    (3.11) in the replay and the shared statistics helper must give the same pin."""
    from stock_machine.agent_intelligence import inference

    def plain_sum(values, start=0):
        total = start
        for v in values:
            total = total + v
        return total
    if sys.version_info >= (3, 12):
        assert plain_sum([0.1] * 10) != sum([0.1] * 10)          # the emulation really differs here
    monkeypatch.setattr(inference, "sum", plain_sum, raising=False)
    monkeypatch.setattr(ir, "sum", plain_sum, raising=False)
    assert ir.behavior_digest.__wrapped__() == PINNED_BEHAVIOR_SHA256


def test_the_draft_protocol_and_behavior_pin_match_this_code_and_nothing_is_registered_yet():
    assert ir.behavior_digest() == ir.BEHAVIOR_SHA256 == PINNED_BEHAVIOR_SHA256
    assert ir.PROTOCOL_SHA256 == PINNED_PROTOCOL_SHA256
    assert ir.REGISTERED_PROTOCOL_SHA256 is None and ir.REGISTERED_DEV_INPUTS is None
    assert ir.REGISTERED_HOLDOUT_INPUTS is None
    assert ir.PROTOCOL["inputs"]["daily_prices_sha256"] == ir.DAILY_PRICES_SHA256
    assert ir.PROTOCOL["inputs"]["earnings_sha256"] == er.EARNINGS_ANNOUNCEMENTS_SHA256
