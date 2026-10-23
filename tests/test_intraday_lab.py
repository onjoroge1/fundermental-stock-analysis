"""Intraday lab: the gap-up pullback and gap-down reversal entries on hand-built bars, exits, the twin, the log rules."""

import importlib.util
import json
import math
from pathlib import Path

import pytest

from stock_machine.backtest import intraday_lab as lab
from stock_machine.backtest import intraday_replay as ir

ROOT = Path(__file__).resolve().parents[1]
GU = {**lab.BASE, "setup": "GAP_UP_PULLBACK"}
GD = {**lab.BASE, "setup": "GAP_DOWN_REVERSAL"}


def day_of(bars, n=390):
    bars = list(bars)
    while len(bars) < n:
        c = bars[-1][3]
        bars.append((c, c, c, c))
    return {"o": [b[0] for b in bars], "h": [b[1] for b in bars], "l": [b[2] for b in bars],
            "c": [b[3] for b in bars], "v": [None if b[0] is None else 1000.0 for b in bars]}


def flat(price, k):
    return [(price, price, price, price)] * k


F = {"prior_close": 100.0, "prior_close_raw": 100.0, "daily_close": 101.0, "beta": 1.0}


# ---- gap up: buy the pullback -------------------------------------------------------

RISE = [(103.0, 103.3, 102.9, 103.2), (103.2, 103.6, 103.1, 103.5), (103.5, 104.0, 103.4, 103.9),
        (103.9, 104.3, 103.8, 104.2), (104.2, 104.5, 104.1, 104.4)]          # 09:30-09:34: high 104.5, 1.46% up


def test_gap_up_limit_buys_half_the_bounce_on_the_way_down_with_its_stop_at_the_open():
    day = day_of(RISE + [(104.4, 104.45, 104.0, 104.1), (104.1, 104.15, 103.7, 103.8)])
    t = lab.gap_up_pullback(day, F, 0.03, {**GU, "entry": "LIMIT_RETRACE"})
    assert (t["entry_bar"], t["entry"], t["stop"], t["kind"]) == (6, 103.75, 103.0, "limit")
    assert t["target"] == pytest.approx(103.75 + 2 * 0.75)
    assert lab.gap_up_pullback(day, F, 0.029, {**GU, "entry": "LIMIT_RETRACE"}) is None
    no_bounce = day_of([(103.0, 103.5, 102.9, 103.4)] * 5 + [(103.4, 103.45, 103.0, 103.1)] * 5)
    assert lab.gap_up_pullback(no_bounce, F, 0.03, {**GU, "entry": "LIMIT_RETRACE"}) is None


def test_gap_up_open_reclaim_needs_a_dip_below_the_open_after_the_bounce():
    dip = [(104.4, 104.4, 103.6, 103.7), (103.7, 103.8, 103.0, 103.1), (103.1, 103.2, 102.8, 102.9),
           (102.9, 103.0, 102.85, 102.95), (102.95, 103.0, 102.9, 102.98)]       # 09:35-09:39: below 103
    back = [(102.98, 103.1, 102.95, 103.05)] * 4 + [(103.05, 103.3, 103.0, 103.25)]  # 09:40-09:44: closes above
    day = day_of(RISE + dip + back + [(103.3, 103.4, 103.2, 103.35)])
    t = lab.gap_up_pullback(day, F, 0.03, {**GU, "entry": "OPEN_RECLAIM"})
    assert (t["entry_bar"], t["entry"], t["stop"], t["kind"]) == (15, 103.3, 102.8, "market")
    stays_above = [(104.4, 104.45, 103.5, 103.6)] * 5 + [(103.6, 103.9, 103.4, 103.8)] * 5
    assert lab.gap_up_pullback(day_of(RISE + stays_above), F, 0.03, {**GU, "entry": "OPEN_RECLAIM"}) is None


def test_gap_up_reversal_bar_after_a_red_bar_closes_above_the_previous_high():
    red = [(104.4, 104.45, 103.9, 104.0)] * 4 + [(104.0, 104.05, 103.8, 103.85)]       # F1 red, low 103.8
    reverse = [(103.85, 104.0, 103.85, 103.95)] * 4 + [(103.95, 104.6, 103.9, 104.5)]  # F2 closes > F1 high 104.45
    day = day_of(RISE + red + reverse + [(104.5, 104.6, 104.4, 104.55)])
    t = lab.gap_up_pullback(day, F, 0.03, {**GU, "entry": "REVERSAL_BAR"})
    assert (t["entry_bar"], t["entry"], t["stop"]) == (15, 104.5, 103.8)        # fires though F2 made a new high
    no_red = day_of(RISE + [(104.4, 104.45, 104.2, 104.42)] * 5 + reverse)
    assert lab.gap_up_pullback(no_red, F, 0.03, {**GU, "entry": "REVERSAL_BAR"}) is None


# ---- gap down: buy the reversal ------------------------------------------------------

DROP = [(97.0, 97.1, 96.5, 96.6), (96.6, 96.7, 96.0, 96.1), (96.1, 96.2, 95.9, 96.0), (96.0, 96.4, 95.95, 96.3),
        (96.3, 97.0, 96.25, 96.9)]                       # low 95.9, bounce to 97.0 (+1.15%)


def test_gap_down_limit_buys_half_the_bounce_with_its_stop_at_the_session_low():
    day = day_of(DROP + [(96.9, 96.95, 96.6, 96.7), (96.7, 96.75, 96.4, 96.5)])
    t = lab.gap_down_reversal(day, F, -0.03, {**GD, "entry": "LIMIT_RETRACE"})
    assert (t["entry_bar"], t["entry"], t["stop"]) == (6, pytest.approx(96.45), 95.9)
    new_low = day_of(DROP + [(96.9, 96.9, 95.5, 95.6)] + flat(95.6, 10))
    assert lab.gap_down_reversal(new_low, F, -0.03, {**GD, "entry": "LIMIT_RETRACE"})["entry"] == pytest.approx(96.45)
    assert lab.gap_down_reversal(day, F, -0.029, {**GD, "entry": "LIMIT_RETRACE"}) is None


def test_gap_down_open_reclaim_and_reversal_bar():
    reclaim = [(96.9, 97.1, 96.85, 97.05)] * 4 + [(97.05, 97.3, 97.0, 97.2)]     # F1 closes above the 97.0 open
    day = day_of(DROP + reclaim + [(97.2, 97.3, 97.1, 97.25)])
    t = lab.gap_down_reversal(day, F, -0.03, {**GD, "entry": "OPEN_RECLAIM"})
    assert (t["entry_bar"], t["entry"], t["stop"]) == (10, 97.2, 95.9)
    red = [(96.9, 96.95, 96.5, 96.6)] * 4 + [(96.6, 96.65, 96.4, 96.45)]         # F1 red after the 97.0 high
    up = [(96.45, 96.6, 96.45, 96.55)] * 4 + [(96.55, 97.0, 96.5, 96.98)]        # F2 closes above F1's 96.95
    day = day_of(DROP + red + up + [(97.0, 97.1, 96.9, 97.05)])
    t = lab.gap_down_reversal(day, F, -0.03, {**GD, "entry": "REVERSAL_BAR"})
    assert (t["entry_bar"], t["entry"], t["stop"]) == (15, 97.0, 96.4)


# ---- exits, scoring and the twin ------------------------------------------------------

def test_exits_stop_first_then_target_then_a_turn_against_then_the_close():
    trade = {"side": 1, "entry_bar": 10, "entry": 100.0, "stop": 99.0, "target": 102.0, "risk": 1.0, "kind": "market"}
    base = flat(100.0, 10) + [(100.0, 100.2, 99.9, 100.1)] * 5
    up = day_of(base + [(100.1, 102.5, 100.0, 102.3)])
    assert lab.lab_exit(up, 1, trade, 99.0, 102.0, 101.0)["reason"] == "TARGET"
    turn = day_of(base + [(100.1, 100.3, 100.05, 100.2)] * 4 + [(100.2, 100.25, 99.6, 99.7), (99.75, 99.8, 99.7, 99.75)])
    out = lab.lab_exit(turn, 1, trade, 99.0, 102.0, 101.0)
    assert (out["reason"], out["exit_bar"], out["exit"]) == ("TURN", 20, 99.75)        # F3 closed below F2's low
    drift = day_of(base + [(100.1, 100.3, 100.05, 100.2)] * 5 + flat(100.25, 300))
    assert lab.lab_exit(drift, 1, trade, 99.0, 102.0, 101.0) == {"exit": 101.0, "exit_bar": None, "reason": "CLOSE",
                                                                 "at_open": False}
    both = day_of(base + [(100.1, 102.5, 98.5, 100.0)])
    assert lab.lab_exit(both, 1, trade, 99.0, 102.0, 101.0)["reason"] == "STOP"


def test_a_limit_fill_minute_is_checked_for_the_stop_only_along_the_path_after_the_fill():
    trade = {"side": 1, "entry_bar": 5, "entry": 100.0, "stop": 99.5, "target": 101.0, "risk": 0.5, "kind": "limit"}
    down_bar = day_of(flat(100.5, 5) + [(100.5, 100.6, 99.4, 99.6)])            # O-H-L-C: falls through both
    assert lab.lab_exit(down_bar, 1, trade, 99.5, 101.0, 101.0)["reason"] == "STOP"
    up_bar = day_of(flat(100.5, 5) + [(100.5, 101.2, 99.4, 101.1)] + flat(100.8, 30))   # O-L-H-C: the low first
    out = lab.lab_exit(up_bar, 1, trade, 99.5, 101.0, 101.0)
    assert out["reason"] == "STOP" and out["exit_bar"] == 5          # the low after the fill, then the high: stopped


def test_the_twin_reflects_the_bracket_and_the_turn_and_costs_are_reported_at_each_level():
    day = day_of(flat(100.0, 10) + [(100.0, 100.2, 99.9, 100.1)] * 5 + [(100.1, 102.5, 100.0, 102.3)])
    spy = {k: [400.0] * 390 for k in "ohlc"}
    trade = {"side": 1, "entry_bar": 10, "entry": 100.0, "stop": 99.0, "target": 102.0, "risk": 1.0, "kind": "market"}
    own = lab.score(trade, day, spy, {**F, "daily_close": 101.0}, {"daily_close": 400.0}, 0.01)
    mirror = lab.score(trade, day, spy, {**F, "daily_close": 101.0}, {"daily_close": 400.0}, 0.01, side=-1)
    assert own["reason"] == "TARGET" and own["r_gross"] == pytest.approx(2.0)
    assert mirror["reason"] == "TURN"            # the short twin: its entry bar closed above the previous bar's high
    assert own["rewards"][0] - own["rewards"][10] == pytest.approx(0.002 / 0.01)


# ---- the log and the splits -------------------------------------------------------------

def test_each_stage_reads_only_its_months_and_needs_the_one_before():
    with pytest.raises(ValueError, match="HOLDOUT"):
        lab.replay(lambda t, m: {}, {"SPY": []}, ("2023-12", "2024-01"), [], {}, split="check")
    with pytest.raises(ValueError, match="EXPLORE_READS_ONLY_THROUGH_2017-06"):
        lab.replay(lambda t, m: {}, {"SPY": []}, ("2017-06", "2017-07"), [], {})
    with pytest.raises(ValueError, match="CONFIRM_READS_ONLY_THROUGH_2019-12"):
        lab.replay(lambda t, m: {}, {"SPY": []}, ("2019-12", "2020-01"), [], {}, split="confirm")
    with pytest.raises(ValueError, match="INVERTED"):
        lab.replay(lambda t, m: {}, {"SPY": []}, ("2024-02", "2016-01"), [], {}, split="check")
    vid = "EXT-FADE.v2"
    sha, code, spec = lab.definition_sha(vid), lab.CODE_SHA, lab.VARIANTS[vid]
    inputs = lab.inputs_sha(["AAPL", "MSFT"])

    def entry(split, **kw):
        return {"definition_sha256": sha, "split": split, "lab_code_sha256": code, "spec": spec,
                "inputs_sha256": inputs, **kw}
    explored = [entry("explore")]
    assert lab.confirm_allowed(vid, [], inputs) == (False, "NOT_EXPLORED_WITH_THIS_CODE")
    assert lab.confirm_allowed(vid, explored, inputs) == (True, "OK")
    assert lab.confirm_allowed(vid, explored, lab.inputs_sha(["AAPL"])) == (False, "NOT_EXPLORED_WITH_THIS_CODE")
    assert lab.confirm_allowed(vid, explored + [entry("confirm", verdict="FAIL")], inputs) == (False, "ALREADY_CONFIRMED")
    assert lab.check_allowed(vid, explored, inputs) == (False, "NOT_CONFIRMED")
    assert lab.check_allowed(vid, explored + [entry("confirm", verdict="FAIL")], inputs) == (False, "NOT_CONFIRMED")
    passed = explored + [entry("confirm", verdict="PASS")]
    assert lab.check_allowed(vid, passed, inputs) == (True, "OK")
    assert lab.check_allowed(vid, passed + [entry("check")], inputs) == (False, "ALREADY_CHECKED")
    renamed = passed + [{**entry("check"), "definition_sha256": "other", "spec": dict(spec)}]
    assert lab.check_allowed(vid, renamed, inputs) == (False, "ALREADY_CHECKED")   # the same strategy under another id
    other_code = [entry("explore"), entry("confirm", verdict="PASS", lab_code_sha256="0" * 64)]
    assert lab.check_allowed(vid, other_code, inputs) == (False, "CONFIRMED_WITH_OTHER_CODE")
    other_words = [entry("explore"), entry("confirm", verdict="PASS", definition_sha256="0" * 64)]
    assert lab.check_allowed(vid, other_words, inputs) == (False, "CONFIRMED_WITH_OTHER_CODE")
    assert lab.check_allowed(vid, passed, lab.inputs_sha(["AAPL"])) == (False, "CONFIRMED_WITH_OTHER_CODE")
    stale = [{**entry("explore"), "lab_code_sha256": "0" * 64}, entry("confirm", verdict="PASS")]
    assert lab.definition_sha(vid, {**spec, "gap": 0.02}) != sha
    assert lab.variants_tried(explored * 3) == 1 and lab.variants_tried(stale) == 2
    assert lab.EXPLORE[1] < lab.CONFIRM[0] and lab.CONFIRM[1] < lab.CHECK[0] and lab.CHECK[1] < lab.HOLDOUT_FROM


def test_log_entries_record_the_definition_code_and_bars():
    evaluation = {"GU-REV.v1": {"trades": 3, "per_trade": {"net_bps_at_10": -1.0, "net_bps_at_2": 2.0, "gross_bps": 4.0},
                                "book": {"at_10": {"mean": 0.1, "lower": -0.1, "upper": 0.3}},
                                "direction_skill": {"mean": 0.0, "lower": -0.2, "upper": 0.2}}}
    entries = lab.log_entries("explore", ("2016-01", "2016-10"), "b" * 64, evaluation, [{"entry": 1}], ["MSFT", "AAPL"])
    e = entries[0]
    assert e["inputs_sha256"] == lab.inputs_sha(["AAPL", "MSFT"]) != lab.inputs_sha(["AAPL"])
    assert e["entry"] == 2 and e["definition_sha256"] == lab.definition_sha("GU-REV.v1")
    assert e["lab_code_sha256"] == lab.CODE_SHA and e["bars_sha256"] == "b" * 64 and e["spec"]["entry"] == "REVERSAL_BAR"
    assert e["spec_sha256"] == lab.spec_sha(lab.VARIANTS["GU-REV.v1"])
    json.dumps(entries)


def test_a_run_refuses_unknown_variants_and_unfinished_periods_and_never_logs_a_refusal(tmp_path):
    log = tmp_path / "log.json"

    def run(split, variants):
        return lab.run(split, variants, dev_root=tmp_path, prices_dir=tmp_path, tickers=["AAPL"], log_path=log)
    assert run("explore", ["NOPE"])["reason"].startswith("UNKNOWN_VARIANT")
    assert run("explore", ["GU-REV.v1"])["reason"] == "NO_FINISHED_MONTH"
    assert run("check", ["GU-REV.v1"])["reason"] == "CHECK_FETCH_INCOMPLETE"
    assert run("confirm", ["GU-REV.v1"])["reason"] == "CONFIRM_FETCH_INCOMPLETE"
    assert not log.exists()
    original = lab.CODE_SHA
    try:
        lab.CODE_SHA = "0" * 64
        assert run("explore", ["GU-REV.v1"])["reason"] == "CODE_CHANGED_SINCE_IMPORT"
    finally:
        lab.CODE_SHA = original
    lab.write_log([{"entry": 1}], log)
    assert lab.read_log(log) == [{"entry": 1}] and not list(tmp_path.glob("*.tmp"))
    spec = importlib.util.spec_from_file_location("run_intraday_lab", ROOT / "scripts" / "run_intraday_lab.py")
    importlib.util.module_from_spec(spec)
    rendered = lab.render({"split": "explore", "months": ["2016-01", "2016-02"], "generated_at": "now", "sessions": 1,
                           "eligible_stock_days": 1, "variants_tried": 6, "variants": {}})
    assert "6 variant tries so far" in rendered and "decide nothing" in rendered
    confirm = lab.render({"split": "confirm", "months": list(lab.CONFIRM), "generated_at": "now", "sessions": 1,
                          "eligible_stock_days": 1, "variants_tried": 7, "confirms_so_far": 1, "variants": {}})
    assert "decide nothing" not in confirm and "1 one-time confirms" in confirm and "decides whether it may be checked" \
        in confirm


def test_the_replay_waits_for_the_lab_looks_before_reading_the_held_back_months():
    def e(split, variant, **kw):
        return {"split": split, "variant": variant, **kw}
    assert lab.replay_reads_through([]) == "2017-06"
    explored = [e("explore", v) for v in lab.FROZEN]
    some = explored + [e("confirm", v, verdict="FAIL") for v in lab.FROZEN[:3]]
    assert lab.replay_reads_through(some) == "2017-06"
    failed = explored + [e("confirm", v, verdict="FAIL") for v in lab.FROZEN]
    assert lab.replay_reads_through(failed) is None
    one_passed = explored + [e("confirm", v, verdict="PASS" if v == "EXT-FADE.v2" else "FAIL") for v in lab.FROZEN]
    assert lab.replay_reads_through(one_passed) == "2019-12"
    assert lab.replay_reads_through(one_passed + [e("check", "EXT-FADE.v2", verdict="FAIL")]) is None
    assert set(lab.FROZEN) <= set(lab.VARIANTS)


def test_the_code_hash_covers_the_daily_price_loader(monkeypatch):
    before = lab.lab_code_sha()
    original = lab.inspect.getsource
    monkeypatch.setattr(lab.inspect, "getsource",
                        lambda fn: original(fn) + ("#changed" if fn is ir.ivr.load_prices else ""))
    assert lab.lab_code_sha() != before


def test_a_limit_filled_at_an_open_through_its_stop_is_stopped_at_once():
    t = lab._trade(GU, 6, 102.9, 103.0, kind="limit")
    assert t["instant_stop"] and lab.lab_exit(day_of(flat(103.0, 10)), 1, t, 103.0, None, 101.0) == {
        "exit": 102.9, "exit_bar": 6, "reason": "STOP", "at_open": True}
    day = day_of(flat(103.0, 6) + [(102.9, 103.0, 102.4, 102.6)])
    spy = {k: [400.0] * 390 for k in "ohlc"}
    twin = lab.score(t, day, spy, F, {"daily_close": 400.0}, 0.01, side=-1)
    assert twin["reason"] == "STOP" and twin["residual"] == 0.0           # the twin is flat at once too
    assert lab._trade(GU, 6, 102.9, 103.0, kind="market") == {"skip": "ENTRY_BEYOND_STOP"}


def test_a_turn_exits_at_the_next_traded_minute_however_late():
    trade = {"side": 1, "entry_bar": 10, "entry": 100.0, "stop": 99.0, "target": 102.0, "risk": 1.0, "kind": "market"}
    bars = flat(100.0, 10) + [(100.0, 100.2, 99.9, 100.1)] * 5 + [(100.1, 100.3, 100.05, 100.2)] * 4 \
        + [(100.2, 100.25, 99.6, 99.7)] + [(None,) * 4] * 6 + [(99.5, 99.6, 99.4, 99.5)]
    out = lab.lab_exit(day_of(bars), 1, trade, 99.0, 102.0, 95.0)
    assert (out["reason"], out["exit_bar"], out["exit"]) == ("TURN", 26, 99.5)


def test_a_new_session_low_counts_only_the_part_of_its_minute_after_the_low():
    fake = [(97.0, 97.1, 96.5, 96.6), (96.6, 96.7, 96.0, 96.1), (96.1, 97.0, 95.9, 96.0)]   # high before the low
    day = day_of(fake + [(96.0, 96.3, 95.95, 96.2), (96.2, 96.4, 96.1, 96.35)] + flat(96.3, 10))
    assert lab.gap_down_reversal(day, F, -0.03, {**GD, "entry": "LIMIT_RETRACE"}) is None
    assert lab.gap_down_reversal(day, F, -0.03, {**GD, "entry": "OPEN_RECLAIM"}) is None


def test_no_market_signal_is_judged_at_the_1029_close():
    climb = [(103.0 + 0.02 * i, 103.06 + 0.02 * i, 102.98 + 0.02 * i, 103.04 + 0.02 * i) for i in range(50)]
    late = climb + [(104.0, 104.02, 103.6, 103.65)] * 5 + [(103.65, 104.3, 103.6, 104.2)] * 5
    assert lab.gap_up_pullback(day_of(late), F, 0.03, {**GU, "entry": "REVERSAL_BAR"}) is None


def test_a_gap_down_reversal_measures_the_pullback_after_a_low_minute_that_closed_up_off_its_low():
    day = day_of([(97.0, 97.2, 95.0, 96.2)] + [(96.2 - 0.01 * i, 96.3, 96.0, 96.1) for i in range(4)]
                 + [(96.1, 96.15, 95.7, 95.8)] * 4 + [(95.8, 96.0, 95.75, 95.78)]
                 + [(95.78, 96.0, 95.76, 95.9)] * 4 + [(95.9, 96.2, 95.85, 96.18)] + flat(96.18, 10))
    t = lab.gap_down_reversal(day, F, -0.03, {**GD, "entry": "REVERSAL_BAR"})
    assert t["stop"] == 95.7                          # the pullback's low, not the 95.0 session low before the "high"


def test_a_cross_is_judged_against_the_last_5_minute_close_that_traded():
    f0 = [(97.0, 97.1, 96.95, 97.0)] * 4 + [(97.0, 97.2, 96.95, 97.1)]   # dips, closes above the open, no bounce yet
    empty = [(None,) * 4] * 5
    f2 = [(97.5, 98.0, 97.5, 97.9)] * 5                                  # the bounce completes, never below the open
    day = day_of(f0 + empty + f2)
    assert lab.gap_down_reversal(day, F, -0.03, {**GD, "entry": "OPEN_RECLAIM"}) is None   # no cross after the gap
    below = [(97.0, 97.1, 96.95, 96.98)] * 5                             # the last close that traded is below the open
    t = lab.gap_down_reversal(day_of(below + empty + f2), F, -0.03, {**GD, "entry": "OPEN_RECLAIM"})
    assert t["entry_bar"] == 15


# ---- the fades frozen from the discovery research ------------------------------------

SPY_FLAT = {k: [400.0] * 390 for k in "ohlc"}
CTX = {"spy": SPY_FLAT, "spy_f": {"prior_close": 400.0, "daily_close": 400.0}, "timing": None}
EXT1, EXT2 = lab.VARIANTS["EXT-FADE.v1"], lab.VARIANTS["EXT-FADE.v2"]
UP_DAY = {**F, "prior_close_raw": 100.0, "close_before_prior": 98.5, "prior_low": 98.0}     # yesterday +1.5%


def test_the_extension_fade_shorts_a_gap_up_after_an_up_day_and_exits_at_the_gap_fill():
    day = day_of([(101.5, 101.6, 101.2, 101.3)] + [(101.3, 101.4, 101.1, 101.2)] * 4
                 + [(101.2, 101.3, 100.9, 101.0)] * 5 + [(101.0, 101.1, 99.9, 100.1)])
    t = lab.extension_fade(day, UP_DAY, 0.015, EXT1, CTX)
    assert (t["side"], t["entry_bar"], t["entry"], t["target"], t["stop"], t["turn"]) == (-1, 1, 101.3, 100.0, None, False)
    out = lab.lab_exit(day, -1, t, None, t["target"], 99.0)
    assert (out["reason"], out["exit"]) == ("TARGET", 100.0)
    assert lab.extension_fade(day, UP_DAY, -0.015, EXT1, CTX) is None              # against yesterday's move
    assert lab.extension_fade(day, {**UP_DAY, "close_before_prior": 99.5}, 0.015, EXT1, CTX) is None   # small prior
    assert lab.extension_fade(day, UP_DAY, 0.009, EXT1, CTX) is None
    loud = {**CTX, "spy": {**SPY_FLAT, "o": [402.4] + [400.0] * 389}}                # SPY gapped 0.6%
    assert lab.extension_fade(day, UP_DAY, 0.015, EXT1, loud)["side"] == -1
    assert lab.extension_fade(day, UP_DAY, 0.015, EXT2, loud) is None
    assert lab.extension_fade(day, UP_DAY, 0.015, EXT2, CTX)["entry_bar"] == 5
    filled = day_of([(101.5, 101.6, 99.9, 100.0)] + flat(100.0, 10))               # the gap filled before entry
    assert lab.extension_fade(filled, UP_DAY, 0.015, EXT1, CTX) == {"skip": "ENTRY_BEYOND_TARGET"}
    no_spy_open = {**CTX, "spy": {**SPY_FLAT, "o": [None] + [400.0] * 389}}
    assert lab.extension_fade(day, UP_DAY, 0.015, EXT1, no_spy_open) == {"skip": "MISSING_SPY_OPEN"}


def test_the_earnings_first_hour_fade_follows_a_first_hour_that_fought_the_gap_from_1030():
    rally = [(97.0, 97.1, 96.8, 97.0)] * 30 + [(97.0, 97.6, 96.9, 97.5)] * 30                 # a 3% gap down, then up
    day = day_of(rally + flat(97.5, 330))
    ctx = {**CTX, "timing": "AFTER_CLOSE"}
    spec = lab.VARIANTS["EARN-FH.v1"]
    t = lab.earnings_first_hour(day, F, -0.03, spec, ctx)
    assert (t["side"], t["entry_bar"], t["entry"], t["target"], t["turn"]) == (1, 60, 97.5, None, False)
    assert lab.earnings_first_hour(day, F, -0.03, spec, {**ctx, "timing": "OTHER"}) is None
    assert lab.earnings_first_hour(day, F, -0.01, lab.VARIANTS["EARN-FH15.v1"], ctx) is None     # under 1.5%
    extended = day_of([(97.0, 97.1, 96.8, 97.0)] * 30 + [(97.0, 97.0, 96.2, 96.3)] * 30 + flat(96.3, 330))
    assert lab.earnings_first_hour(extended, F, -0.03, spec, ctx) is None                # the first hour followed the gap
    half = {**ctx, "spy": {k: v[:210] for k, v in SPY_FLAT.items()}}                    # a half session by the calendar
    assert lab.earnings_first_hour(day, F, -0.03, spec, half) is None
    assert lab.earnings_first_hour(day_of(rally + flat(97.5, 100)), F, -0.03, spec, ctx)["side"] == 1   # stock's gaps
    no_spy_open = {**ctx, "spy": {**SPY_FLAT, "o": [None] + [400.0] * 389}}
    assert lab.earnings_first_hour(day, F, -0.03, spec, no_spy_open) == {"skip": "MISSING_SPY_OPEN"}
    for n in (16, 59):                                    # stopped trading before 10:29: lists end at the last trade
        short = {k: v[:n] for k, v in day.items()}
        assert lab.earnings_first_hour(short, F, -0.03, spec, ctx) == {"skip": "MISSING_MINUTE"}
    out = lab.lab_exit(day, 1, t, None, None, 98.0)
    assert (out["reason"], out["exit"]) == ("CLOSE", 98.0)


def trade(month, session, net):
    return {"month": month, "session": session, "net_bps": {5: net}}


def test_the_pass_bar_needs_the_mean_the_session_t_and_two_of_three_periods():
    good = [trade(m, f"{m}-{d:02d}", 20.0 + (d % 3) * 5) for m in ("2017-08", "2018-03", "2019-05") for d in range(1, 21)]
    pb = lab.pass_bar(good)
    assert pb["verdict"] == "PASS" and pb["positive_periods"] == 3 and pb["session_t"] > 2
    assert pb["sessions"] == 60 and pb["largest_session_share"] == pytest.approx(1 / 60)
    one_year = [trade("2018-03", f"2018-03-{d:02d}", 30.0 + d) for d in range(1, 25)] + \
        [trade("2019-05", f"2019-05-{d:02d}", -5.0 - d % 2) for d in range(1, 25)]
    assert lab.pass_bar(one_year)["verdict"] == "FAIL"                              # only 2018 positive
    noisy = [trade("2018-03", f"2018-03-{d:02d}", 300.0 if d % 2 else -290.0) for d in range(1, 21)]
    assert lab.pass_bar(noisy)["verdict"] == "FAIL"                                 # t far under 2
    assert lab.pass_bar(good, "explore")["verdict"] is None                         # reference only


def test_the_session_t_is_the_plain_t_with_one_trade_a_session_and_one_session_cannot_carry_it():
    xs = [3.0, -1.0, 4.0, 1.0, 5.0, -2.0, 6.0]
    m = sum(xs) / len(xs)
    plain = m / (math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) / math.sqrt(len(xs)))
    assert lab.session_t(xs, [str(i) for i in range(len(xs))]) == pytest.approx(plain)
    # one news day with 20 identical winners and 10 ordinary days: per trade t is 7, by session it is under 2
    big = [trade("2018-03", "2018-03-01", 200.0)] * 20 + \
        [trade("2018-03", f"2018-03-{d:02d}", -10.0 + (d % 2) * 15) for d in range(2, 12)]
    values = [t["net_bps"][5] for t in big]
    assert lab.session_t(values, [str(i) for i in range(len(values))]) > 6
    assert lab.session_t(values, [t["session"] for t in big]) < 2
    two = [trade("2018-02", "2018-02-06", 200.0)] * 20 + [trade("2019-04", "2019-04-02", 1.0)]
    assert lab.pass_bar(two)["verdict"] == "FAIL"                                   # two sessions, one doing the work
    assert lab.session_t([1.0, 2.0], ["a", "a"]) is None


def test_the_check_bar_needs_three_of_the_four_years_2020_to_2023():
    years = {"2020": 20.0, "2021": 25.0, "2022": -5.0, "2023": 30.0}
    trades = [trade(f"{y}-03", f"{y}-03-{d:02d}", v + (d % 3) * 4) for y, v in years.items() for d in range(1, 21)]
    pb = lab.pass_bar(trades, "check")
    assert list(pb["periods"]) == [f"{y}-01..{y}-12" for y in years] and pb["positive_periods"] == 3
    assert pb["verdict"] == "PASS"
    two = [t for t in trades if not t["month"].startswith("2023")]
    assert lab.pass_bar(two, "check")["verdict"] == "FAIL"


def test_a_short_is_scored_as_a_short_and_its_twin_as_a_long():
    day = day_of([(101.5, 101.6, 101.2, 101.3)] + [(101.3, 101.4, 100.4, 100.5)] * 5 + flat(100.5, 300))
    t = lab.extension_fade(day, UP_DAY, 0.015, EXT1, CTX)
    own = lab.score(t, day, SPY_FLAT, {**F, "daily_close": 100.5}, {"daily_close": 400.0}, 0.01)
    twin = lab.score(t, day, SPY_FLAT, {**F, "daily_close": 100.5}, {"daily_close": 400.0}, 0.01, side=1)
    assert own["residual"] == pytest.approx(-(100.5 / 101.3 - 1)) and own["residual"] > 0
    assert twin["residual"] == pytest.approx(100.5 / 101.3 - 1)


def test_the_lab_replay_excludes_bad_daily_rows_and_spy_errors_like_the_replay():
    bars_for, prices, events, tickers, _ = ir.behavior_fixture()
    asked = set()

    def counting(ticker, month):
        asked.add(month)
        return bars_for(ticker, month)
    run = lab.replay(counting, prices, ir.BEHAVIOR_MONTHS, tickers, lab.VARIANTS, split="confirm", events=events)
    assert set(ir.detection_months(ir.BEHAVIOR_MONTHS)) == asked          # 2018-10 on, only to find contradicted closes
    assert run["excluded"]["SPY_DAILY_ERROR"] == 18 and run["excluded"]["DAILY_ROW_ERROR"] == 3
    assert run["excluded"]["DAILY_CLOSE_MISMATCH"] == 3
    assert run["trades"] and not any(t["session"] in ("2019-03-27", "2019-03-28", "2019-03-29") for t in run["trades"])
