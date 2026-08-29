from stock_machine.backtest.meta_model import rolling_weights


def test_ensemble_is_equal_weighted_before_enough_oos_history():
    w = rolling_weights({"ridge": [0.2, 0.1], "lightgbm": [0.4, 0.3]})
    assert w == {"lightgbm": 0.5, "ridge": 0.5}


def test_ensemble_weights_only_positive_trailing_oos_edge():
    w = rolling_weights({
        "ridge": [0.10, 0.05, 0.08, 0.07, 0.06],
        "lightgbm": [-0.10, -0.05, -0.02, -0.03, -0.01],
    })
    assert w["ridge"] == 1.0
    assert w["lightgbm"] == 0.0


def test_ensemble_falls_back_to_equal_when_all_recent_edge_is_nonpositive():
    w = rolling_weights({
        "ridge": [-0.1, -0.1, -0.1, -0.1],
        "lightgbm": [-0.2, -0.2, -0.2, -0.2],
    })
    assert w == {"lightgbm": 0.5, "ridge": 0.5}
