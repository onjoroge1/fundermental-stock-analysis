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
