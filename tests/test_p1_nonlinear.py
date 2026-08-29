from stock_machine.backtest.nonlinear_model import _new_model


def test_p1_lightgbm_dependency_is_available_in_ci():
    model = _new_model()
    assert model is not None
    x = [[i, i % 3] for i in range(100)]
    y = [0.2 * i + (i % 3) for i in range(100)]
    model.fit(x, y)
    pred = model.predict([[50, 2]])
    assert len(pred) == 1
