import pytest

from stock_machine.forecasts.calibration import (
    apply_isotonic,
    balanced_accuracy,
    calibration_error,
    fit_isotonic,
    quantile,
)


def test_quantile_interpolates_instead_of_selecting_upper_middle():
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([1, 2, 3, 4], 0.25) == 1.75


def test_isotonic_calibrator_is_monotonic_and_bounded():
    fitted = fit_isotonic(
        [0.2, 0.35, 0.5, 0.65, 0.8, 0.9],
        [False, True, False, True, True, True],
    )
    calibrated = [apply_isotonic(p, fitted) for p in [0.1, 0.3, 0.6, 0.95]]
    assert calibrated == sorted(calibrated)
    assert all(0 <= value <= 1 for value in calibrated)
    assert fitted["sample_size"] == 6


def test_probability_metrics_do_not_confuse_accuracy_with_calibration():
    probabilities = [0.1, 0.4, 0.6, 0.9]
    outcomes = [False, False, True, True]
    assert balanced_accuracy(probabilities, outcomes) == 1.0
    assert calibration_error(probabilities, outcomes, bins=2) == pytest.approx(0.25)
