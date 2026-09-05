"""Dependence-aware uncertainty for chronological research scores."""
from math import sqrt
from statistics import NormalDist


def mean_uncertainty(values, *, lags=0, alpha=0.05):
    values = list(values)
    n = len(values)
    if not n:
        return {"n": 0, "status": "INSUFFICIENT_DATA"}
    mean = sum(values) / n
    lag_count = min(max(0, lags), n - 1)
    centered = [x - mean for x in values]
    variance = sum(x*x for x in centered) / n
    for lag in range(1, lag_count + 1):
        covariance = sum(centered[i] * centered[i-lag] for i in range(lag, n)) / n
        variance += 2 * (1 - lag / (lag_count + 1)) * covariance
    se = sqrt(max(0.0, variance) / max(1, n - 1))
    enough = n >= max(12, 4 * (lag_count + 1)) and se > 1e-12
    critical = NormalDist().inv_cdf(1 - alpha / 2)
    return {"n": n, "mean": mean, "status": "OK" if enough else "INSUFFICIENT_DATA",
            "method": "Newey-West (Bartlett)", "lags": lag_count,
            "standard_error": se, "tstat": mean/se if enough else None,
            "lower": mean-critical*se if enough else None,
            "upper": mean+critical*se if enough else None}
