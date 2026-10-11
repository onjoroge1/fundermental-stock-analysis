"""Inference shared by the pre-registered promotion tests (v2) and their power tooling.

Block-level statistics from these panels are autocorrelated: adjacent
20-session blocks share outcome windows and persistent signals (lag-1
autocorrelation about +0.16 in simulation). An independent-block bootstrap
understated the spread by 22-26% and passed 5-7% of the time with no real
improvement; a Newey-West lag-1 standard error with a t(n-1) cutoff came to
about 3% from 24 blocks (docs/evidence/promotion-test-power.md).
"""

from __future__ import annotations

from math import sqrt

Z_975 = 1.959964
Z_80 = 0.841621


def t_quantile_975(df: int) -> float:
    """Two-sided 95% Student-t quantile (Cornish-Fisher; ~1e-4 for df >= 10)."""
    if df < 1:
        raise ValueError("T_DEGREES_OF_FREEDOM_INVALID")
    z = Z_975
    return (
        z
        + (z**3 + z) / (4 * df)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3)
    )


def newey_west_se(values: list[float], lag: int = 1) -> float:
    """Bartlett-weighted HAC standard error of a mean, small-sample (n-1) scaled."""
    n = len(values)
    if n < 2:
        raise ValueError("NEWEY_WEST_NEEDS_TWO_VALUES")
    m = sum(values) / n
    e = [v - m for v in values]
    var = sum(x * x for x in e) / n
    for l in range(1, lag + 1):
        var += 2 * (1 - l / (lag + 1)) * sum(e[i] * e[i + l] for i in range(n - l)) / n
    return sqrt(max(var, 0.0) / (n - 1))


def jackknife_pseudo_values(statistic, n: int) -> list[float]:
    """Delete-one-block pseudo-values of a non-additive statistic (e.g. an IC)."""
    full = statistic(list(range(n)))
    return [n * full - (n - 1) * statistic([j for j in range(n) if j != i]) for i in range(n)]


def lower_bound(estimate: float, se: float, n: int) -> float:
    return estimate - t_quantile_975(n - 1) * se


def detectable_difference(se: float, n: int) -> float:
    """True difference this test detects with 80% power at its own noise level."""
    return (t_quantile_975(n - 1) + Z_80) * se
