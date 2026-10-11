"""Shared inference for the v2 promotion tests."""

import pytest

from stock_machine.agent_intelligence import inference


@pytest.mark.parametrize("df,expected", [(11, 2.2010), (23, 2.0687), (35, 2.0301), (200, 1.9719)])
def test_t_quantile_matches_tables(df, expected):
    assert inference.t_quantile_975(df) == pytest.approx(expected, abs=2e-4)


def test_newey_west_reflects_autocorrelation():
    alternating, trending = [1.0, -1.0] * 12, [1.0] * 12 + [-1.0] * 12
    assert inference.newey_west_se(trending) > inference.newey_west_se(trending, lag=0)
    assert inference.newey_west_se(alternating) < inference.newey_west_se(alternating, lag=0)
    with pytest.raises(ValueError):
        inference.newey_west_se([1.0])


def test_jackknife_pseudo_values_recover_a_mean():
    values = [1.0, 2.0, 4.0, 7.0]
    pseudo = inference.jackknife_pseudo_values(lambda idx: sum(values[i] for i in idx) / len(idx), 4)
    assert pseudo == pytest.approx(values)


def test_bounds_and_detectable_difference_use_t():
    assert inference.lower_bound(1.0, 0.1, 24) == pytest.approx(1.0 - 2.0687 * 0.1, abs=1e-4)
    assert inference.detectable_difference(0.1, 24) == pytest.approx((2.0687 + 0.8416) * 0.1, abs=1e-4)
