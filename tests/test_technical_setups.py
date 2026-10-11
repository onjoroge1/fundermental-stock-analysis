from copy import deepcopy
from datetime import datetime, timezone
from math import sin
import pytest
from stock_machine.agent_intelligence import technical_setups as t
from stock_machine.market_calendar import session_dates


def bars(count=420):
    dates = session_dates("2023-01-03", "2026-10-07")[-count:]
    result = []
    for i, day in enumerate(dates):
        c = 100 + 0.04 * i + 2 * sin(i / 7)
        result.append(
            {
                "date": day,
                "open": c - 0.2,
                "high": c + 1,
                "low": c - 1,
                "close": c,
                "adj_close": c,
                "volume": 1000000.0,
            }
        )
    return result


def test_setups_and_combinations_have_explicit_context():
    rows = [
        {
            "date": str(i),
            "open": 10.0,
            "close": 10.0,
            "high": 10.1,
            "low": 9.9,
            "volume": 100.0,
            "adjustment_factor": 1.0,
        }
        for i in range(60)
    ]
    rows[-1].update(open=10.9, close=11.0, high=11.1, low=9.0, volume=200.0)
    f = t.features(rows)
    assert f["signals"]["volume_breakout"] == 1
    assert f["signals"]["rejection"] == 1 and f["signals"]["pullback_rejection"] == 1
    assert f["lower_wick_ratio"] > 0.6
    rows[-5]["adjustment_factor"] = 0.5
    assert t.features(rows)["volume_confirmation_ratio"] is None


def test_current_features_ignore_future_rows_and_reject_bad_ohlcv():
    rows = bars()
    cutoff = rows[-30]["date"]
    before = t.examples(rows, cutoff, 5)
    changed = deepcopy(rows)
    for r in changed[-29:]:
        r["adj_close"] = 99999
    assert t.examples(changed, cutoff, 5) == before
    broken = deepcopy(rows)
    broken[-1]["high"] = 1
    assert t.examples(broken, rows[-1]["date"], 5)[1] is None
    gapped = rows[:-20] + rows[-19:]
    assert t.examples(gapped, rows[-1]["date"], 5)[1] is None


def test_next_close_entry_full_horizon_and_adjustment_invariance():
    rows = bars()
    samples, current = t.examples(rows, rows[-1]["date"], 5)
    s = samples[0]
    bydate = {r["date"]: r for r in rows}
    assert s["entry"] > s["origin"] and s["due"] > s["entry"]
    assert s["return"] == pytest.approx(
        bydate[s["due"]]["adj_close"] / bydate[s["entry"]]["adj_close"] - 1
    )
    scaled = deepcopy(rows)
    for r in scaled:
        r["adj_close"] *= 0.5
    other, _ = t.examples(scaled, rows[-1]["date"], 5)
    assert [s["return"] for s in other] == pytest.approx([s["return"] for s in samples])
    assert other[0]["signals"] == samples[0]["signals"]


def test_walk_forward_purges_labels_across_every_stock(monkeypatch):
    monkeypatch.setitem(t.POLICY, "minimum_train_sessions", 40)
    monkeypatch.setitem(t.POLICY, "train_sessions", 60)
    monkeypatch.setitem(t.POLICY, "test_sessions", 20)
    rows = bars()
    samples, _ = t.examples(rows, rows[-1]["date"], 20)
    pooled = samples + deepcopy(samples)
    base = t.walk_forward(samples, pooled, rows[-1]["date"])
    first = base["folds"][0]["test_start"]
    assert all(f["train_max_due"] < f["test_start"] for f in base["folds"])
    changed = deepcopy(pooled)
    for s in changed:
        if s["due"] >= first:
            s["return"] = 1000
    other = t.walk_forward(samples, changed, rows[-1]["date"])
    assert base["folds"][0]["weights"] == other["folds"][0]["weights"]
    candidate = base["candidate"]
    assert sum(candidate["weights"].values()) == pytest.approx(
        1 if candidate["status"] == "SHADOW_CANDIDATE" else 0
    )
    assert all(v >= 0 for v in candidate["weights"].values())
    assert "equal_mix" in base["oos"] and "buy_and_hold" in base["oos"]
    assert len({s["origin"] for s in base["oos_predictions"]}) == len(
        base["oos_predictions"]
    )


def test_costs_abstentions_and_cold_start_are_visible():
    m = t.metrics([{"action": 1, "return": 0.01}, {"action": 0, "return": 1}])
    assert m["trades"] == 1 and m["mean_net_return"] == pytest.approx(0.008)
    assert m["mean_net_return_per_opportunity"] == pytest.approx(0.004)
    assert t.fit([], [])["status"] == "INSUFFICIENT_EVIDENCE"
    assert t.action({n: 1 for n in t.NAMES}, t.fit([], [])["weights"]) == 0


def test_negative_training_edge_abstains_and_cached_folds_match():
    dates = session_dates("2023-01-03", "2026-10-07")[-400:]
    samples = [
        {
            "origin": d,
            "entry": dates[i + 1],
            "due": dates[i + 6],
            "return": -0.01,
            "signals": {n: 1 for n in t.NAMES},
        }
        for i, d in enumerate(dates[:-6])
    ]
    result = t.fit(samples, samples)
    assert result["status"] == "NO_POSITIVE_TRAINING_EDGE"
    assert sum(result["weights"].values()) == 0
    direct = t.walk_forward(samples, samples, dates[-1])
    cached = t.walk_forward(
        samples, samples, dates[-1], context=t.prepare_walk_forward(samples, dates[-1])
    )
    assert direct == cached


def overlapping_samples(returns, horizon=5, start="2023-01-03"):
    dates = session_dates(start, "2026-10-07")
    return [
        {"origin": dates[i], "entry": dates[i + 1], "due": dates[i + 1 + horizon],
         "return": r, "signals": {n: (1 if n == "trend" else 0) for n in t.NAMES}}
        for i, r in enumerate(returns)
    ]


def test_effective_windows_count_non_overlapping_holds_and_dates_once():
    samples = overlapping_samples([0.01] * 60, horizon=5)
    # 60 consecutive daily signals share windows: one per 6 sessions.
    assert t.non_overlapping_windows(samples) == 10
    stock_b = [{**s, "return": -0.01} for s in samples]
    stats = t.pooled_statistics(samples + stock_b)
    # Two stocks on the same dates are one market observation per date.
    assert stats["trend"]["windows"] == 10 and stats["trend"]["active"] == 120


def test_realistic_edge_is_no_longer_rejected_by_per_trade_dispersion():
    # Mean net edge 1.8% per 5-session trade with 6% dispersion: v1 utility
    # (mean - 0.5*std) was negative; the standard-error rule finds the edge.
    samples = overlapping_samples([0.08 if i % 2 else -0.04 for i in range(400)])
    values = [s["return"] - t.POLICY["round_trip_cost"] for s in samples]
    from statistics import mean, pstdev
    assert mean(values) - 0.5 * pstdev(values) < 0
    result = t.fit(samples, samples)
    assert result["status"] == "SHADOW_CANDIDATE"
    assert result["weights"]["trend"] == pytest.approx(1.0)
    assert result["counts"]["trend"]["stock_windows"] == result["counts"]["trend"]["pooled_windows"] == 67


def test_thin_evidence_does_not_act_on_a_noisy_positive_mean():
    # Same mean, but only a few independent windows: the uncertainty dominates.
    samples = overlapping_samples([0.20 if i % 2 else -0.16 for i in range(40)])
    stats = t.pooled_statistics(overlapping_samples([0.20 if i % 2 else -0.16 for i in range(40)]))
    stats["trend"]["dates"] = t.POLICY["minimum_active"]
    assert t.fit(samples, [], pooled_stats=stats)["status"] == "NO_POSITIVE_TRAINING_EDGE"


def test_policy_change_produces_new_versioned_runs():
    assert t.POLICY["standard_error_penalty"] == 1.645
    assert "risk_penalty" not in t.POLICY
    assert t.POLICY_HASH == t.digest(t.POLICY)


def test_metrics_report_intervals_on_independent_trades():
    samples = overlapping_samples([0.08 if i % 2 else -0.04 for i in range(120)])
    values = [{**s, "action": 1} for s in samples]
    m = t.metrics(values)
    assert m["trades"] == 120 and m["effective_trades"] == t.non_overlapping_windows(samples) == 20
    low, high = m["mean_net_return_ci95"]
    assert low < m["mean_net_return"] < high
    # Width reflects 20 independent windows, not 120 overlapping trades.
    assert (high - low) == pytest.approx(2 * 1.959964 * m["return_std"] / 20 ** 0.5)
    wl, wh = m["net_win_rate_ci95"]
    assert wl < m["net_win_rate"] < wh


def test_placebo_flips_by_date_shared_across_stocks_and_is_deterministic():
    a = overlapping_samples([0.01] * 30)
    b = [{**s, "return": 0.02} for s in a]
    pa, pb = t.placebo(a), t.placebo(b)
    assert pa == t.placebo(a)
    assert all((x["return"] > 0) == (y["return"] > 0) for x, y in zip(pa, pb))
    assert {x["return"] > 0 for x in pa} == {True, False}


def test_walk_forward_reports_how_often_a_no_edge_fit_would_act():
    samples = overlapping_samples([0.08 if i % 2 else -0.04 for i in range(700)])
    result = t.walk_forward(samples, samples, samples[-1]["due"])
    placebo = result["placebo"]
    assert placebo["folds"] == len(result["folds"]) > 0
    assert placebo["acting_folds"] <= placebo["folds"]
    # The real edge acts; the sign-randomized placebo should rarely do so.
    assert result["candidate"]["status"] == "SHADOW_CANDIDATE"
    assert placebo["act_rate"] < 0.5
