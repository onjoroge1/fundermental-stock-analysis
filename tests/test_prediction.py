import math
import random

from stock_machine.prediction import (bootstrap_paths, log_returns,
                                      make_windows, summarize_paths,
                                      train_stats, validate)


def _series(n=1200, drift=0.0004, vol=0.015, seed=3):
    rng = random.Random(seed)
    px, out = 100.0, []
    for _ in range(n):
        px *= math.exp(rng.gauss(drift, vol))
        out.append(px)
    return out


def test_log_returns_and_stats():
    rets = log_returns([100, 110, 99])
    assert math.isclose(rets[0], math.log(1.1))
    m, s = train_stats(rets)
    assert s > 0


def test_windows_shapes_no_overlap_into_target():
    z = list(range(100))
    xs, ys = make_windows(z, 40)
    assert len(xs) == 60 and len(xs[0]) == 40
    assert ys[0] == 40  # target strictly after the window


def test_scaling_is_train_only():
    """The leak the Kaggle notebook commits: stats must come from the train
    slice, and adding test data must not change them."""
    rets = log_returns(_series())
    train = rets[:800]
    m1, s1 = train_stats(train)
    m2, s2 = train_stats(train + [0.5] * 50)  # extreme 'test' data
    assert (m1, s1) == train_stats(train)
    assert m2 != m1  # proves the stat is sensitive — so fitting on all leaks


def test_bootstrap_probabilities_track_drift():
    up = log_returns(_series(drift=0.002))
    down = log_returns(_series(drift=-0.002))
    p_up = summarize_paths(bootstrap_paths(up, 252, n_paths=200), 100.0)
    p_dn = summarize_paths(bootstrap_paths(down, 252, n_paths=200), 100.0)
    assert p_up["horizons"]["12m"]["prob_positive"] > 0.6
    assert p_dn["horizons"]["12m"]["prob_positive"] < 0.4


def test_percentiles_monotonic_and_fan_widens():
    rets = log_returns(_series())
    s = summarize_paths(bootstrap_paths(rets, 252, n_paths=200), 100.0)
    for h in s["horizons"].values():
        assert h["p10"] <= h["p25"] <= h["p50"] <= h["p75"] <= h["p90"]
    width = [f["p90"] - f["p10"] for f in s["fan"]]
    assert width[-1] > width[0]  # uncertainty must grow with horizon


def test_bootstrap_deterministic_with_seed():
    rets = log_returns(_series())
    a = bootstrap_paths(rets, 21, n_paths=50, seed=9)
    b = bootstrap_paths(rets, 21, n_paths=50, seed=9)
    assert a == b


def test_bootstrap_accepts_exactly_one_full_block():
    returns = [float(i) / 10_000 for i in range(21)]
    assert bootstrap_paths(returns, 21, n_paths=2, block=21) == [
        returns, returns
    ]


def test_even_sample_median_is_interpolated():
    # The shortest configured horizon is five days.
    paths = [[math.log(value / 100.0)] + [0.0] * 4
             for value in (90.0, 100.0, 110.0, 120.0)]
    summary = summarize_paths(paths, 100.0)
    assert summary["horizons"]["5d"]["p50"] == 105.0


def test_drift_neutral_bootstrap_centers_on_50pct():
    """Demeaned resampling must remove directional bias: P(up) ≈ 0.5 even
    for a strongly drifting series."""
    rets = log_returns(_series(drift=0.002))  # strong uptrend
    m = sum(rets) / len(rets)
    demeaned = [r - m for r in rets]
    s = summarize_paths(bootstrap_paths(demeaned, 252, n_paths=300), 100.0)
    p = s["horizons"]["12m"]["prob_positive"]
    assert 0.40 <= p <= 0.60, f"drift-neutral P(up) should be ~0.5, got {p}"


def test_validation_defaults_to_drift_neutral_without_proven_edge(monkeypatch):
    import stock_machine.prediction as prediction

    monkeypatch.setattr(prediction, "TORCH_OK", False)
    returns = [0.001 + (0.002 if i % 2 else -0.002) for i in range(1200)]
    result = validate(returns)
    assert result["n_folds"] >= 5
    assert result["purge_days"] == 20
    assert result["horizons_days"] == [5, 10, 20]
    assert result["verdict"]["primary_model"] == "bootstrap_drift_neutral"
    assert "trend" in result["bootstrap"]["by_regime"]
    assert result["bootstrap"]["by_regime"]["earnings_proximity"][
        "status"
    ] == "unavailable"
    assert result["bootstrap"]["signed_bias_pct"] > result[
        "bootstrap_drift_neutral"
    ]["signed_bias_pct"]


def test_forecast_has_no_filesystem_side_effects(tmp_path, monkeypatch):
    """Computation is worker-owned and never creates a local request cache."""
    import stock_machine.prediction as P
    monkeypatch.chdir(tmp_path)
    closes = [{"date": "2026-08-07", "adj_close": 100.0}]  # newer than cache
    r = P.forecast("ZZZ", closes)
    assert r.get("status") == "INSUFFICIENT_DATA"
    assert list(tmp_path.iterdir()) == []
