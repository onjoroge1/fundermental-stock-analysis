"""Event strategies: point-in-time entries, path exits, pinned behaviour."""

from math import sqrt

import pytest

from stock_machine.agent_intelligence import strategy_signals as s

PINNED_BEHAVIOR = "82103fafbce22ef7a06c3c9cf4154a67380f87f8819b10319ea61f2291ee39be"


def bars_for(**spec):
    rows, days = s._fixture_bars(**spec)
    return s.Bars(rows, days[-1], days), rows, days


BREAKOUT = dict(drift=0.001, wobble=0.004, start=80.0, spike_at=150, spike_volume=3.0, gap_at=150, gap=0.06)


def test_volume_breakout_needs_volume_and_a_rising_50_day_average():
    b, _, _ = bars_for(**BREAKOUT)
    assert s.s1_entry(b, 150)
    quiet, _, _ = bars_for(**{**BREAKOUT, "spike_volume": 1.0})
    assert not s.s1_entry(quiet, 150)                       # no volume confirmation
    falling, _, _ = bars_for(**{**BREAKOUT, "drift": -0.002})
    assert not s.s1_entry(falling, 150)                     # 50-day average not rising / price below it


def test_pullback_entry_requires_a_strong_uptrend():
    b, _, days = bars_for(drift=0.002, wobble=0.012, start=50.0)
    entries = [i for i in range(60, len(days)) if s.s2_entry(b, i)]
    assert entries
    for i in entries:
        assert b.rising50(i) and b.momentum(i) > 0 and b.close[i] > b.sma20[i] > b.sma50[i]
        assert b.bars[i]["low"] <= b.sma20[i] and b.close[i] > b.close[i - 1]


def test_atr_is_computed_on_normalized_bars():
    rows, days = s._fixture_bars(drift=0.0, wobble=0.0, start=100.0, length=80)
    for r in rows:   # a dividend-style adjustment: adjusted close below the raw close
        r["adj_close"] = r["close"] * 0.9
    b = s.Bars(rows, days[-1], days)
    # high 1.01x, low 0.985x of a flat price: true range is 2.5% of the adjusted price
    assert b.atr[-1] == pytest.approx(0.025 * 100.0 * 0.9)


def test_a_missing_bar_voids_indicators_that_touch_it():
    rows, days = s._fixture_bars(drift=0.001, wobble=0.01, start=50.0, length=140)
    gap_day = days[100]
    b = s.Bars([r for r in rows if r["date"] != gap_day], days[-1], days)
    assert b.close[100] is None and b.sma20[110] is None and b.sma20[99] is not None
    assert b.sma20[139] is not None and b.sma50[139] is None          # 50-day window still spans the gap
    assert b.window(139) is None and not s.s1_entry(b, 139)


def path(closes, *, ranges=0.0):
    days = [f"2024-01-{d:02d}" for d in range(1, 32)][:len(closes)]
    rows = [{"date": d, "open": c, "high": c * (1 + ranges), "low": c * (1 - ranges), "close": c, "adj_close": c,
             "volume": 1.0} for d, c in zip(days, closes)]
    return s.Bars(rows, days[-1], days), days


def test_trailing_stop_is_judged_on_the_close_and_executed_next_session():
    closes = [100, 101, 103, 105, 104, 98, 97, 96] + [96] * 20
    b, _ = path(closes)
    exit_i, reason = s.simulate_exit(b, 0, "S1_volume_breakout", atr_ref=2.0)
    # peak 105, stop at 101: the close of 98 (index 5) breaches it, exit at the next close (index 6)
    assert (exit_i, reason) == (6, "TRAILING_STOP")


def test_gap_fill_and_max_hold():
    closes = [100] * 3 + [110, 112, 111, 99.5] + [100] * 30
    b, _ = path(closes)
    assert s.simulate_exit(b, 3, "S3_post_earnings", atr_ref=50.0, gap_level=100.0) == (7, "GAP_FILL")
    steady, _ = path([100 + i * 0.1 for i in range(30)])
    assert s.simulate_exit(steady, 0, "S1_volume_breakout", atr_ref=5.0) == (20, "MAX_HOLD")


def test_trend_failure_exit():
    b, _, days = bars_for(drift=0.002, wobble=0.006, start=40.0, turn_at=140, drift_after=-0.001)
    reasons = {s.simulate_exit(b, i + 1, "S2_trend_pullback", atr_ref=b.atr[i])[1]
               for i in range(60, len(days) - 1) if s.s2_entry(b, i)}
    assert "TREND_FAILURE" in reasons


def test_incomplete_or_missing_paths_are_never_scored():
    closes = [100 + i for i in range(30)]
    b, days = path(closes)
    assert s.simulate_exit(b, 15, "S1_volume_breakout", atr_ref=5.0) == ("PENDING", None)
    rows = [{"date": d, "open": c, "high": c, "low": c, "close": c, "adj_close": c, "volume": 1.0}
            for d, c in zip(days, closes) if d != days[5]]
    gappy = s.Bars(rows, days[-1], days)
    assert s.simulate_exit(gappy, 1, "S1_volume_breakout", atr_ref=5.0) == ("EXCLUDED_MISSING_BARS", None)


def test_reaction_z_is_beta_adjusted_and_volatility_scaled():
    stock, _ = path([100, 100, 110, 112] + [112] * 5)
    spy, _ = path([400, 400, 404, 408] + [408] * 5)
    z = s.reaction_z(stock, spy, 2, beta=1.5, vol20=0.3)
    expected = ((112 / 100 - 1) - 1.5 * (408 / 400 - 1)) / (0.3 * sqrt(2 / 252))
    assert z == pytest.approx(expected)
    assert s.reaction_z(stock, spy, 2, beta=9.0, vol20=None) == pytest.approx(
        ((112 / 100 - 1) - 3.0 * (408 / 400 - 1)) / (0.05 * sqrt(2 / 252)))   # beta clipped, vol floored
    assert s.reaction_z(stock, spy, 8, beta=1.0, vol20=0.3) is None             # reaction window incomplete


def test_no_rule_reads_a_bar_after_the_session_it_judges():
    b, rows, days = bars_for(drift=0.002, wobble=0.012, start=50.0)
    entries = [i for i in range(60, len(days) - 25) if s.s2_entry(b, i)]
    assert entries
    for i in entries[:5]:
        exit_i, reason = s.simulate_exit(b, i + 1, "S2_trend_pullback", atr_ref=b.atr[i])
        cut = exit_i if isinstance(exit_i, int) else i
        altered = [dict(r, close=r["close"] * 0.5, adj_close=r["adj_close"] * 0.5, low=r["low"] * 0.5)
                   if r["date"] > days[cut] else r for r in rows]
        b2 = s.Bars(altered, days[-1], days)
        assert s.s2_entry(b2, i)
        assert s.simulate_exit(b2, i + 1, "S2_trend_pullback", atr_ref=b2.atr[i]) == (exit_i, reason)


def test_behavior_is_pinned():
    assert s.behavior_digest() == PINNED_BEHAVIOR
