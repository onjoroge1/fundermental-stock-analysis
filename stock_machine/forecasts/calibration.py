"""Small, dependency-free helpers for honest probabilistic forecasts."""
from __future__ import annotations

import bisect
import math
from collections.abc import Iterable


def quantile(values: Iterable[float], probability: float) -> float:
    """Return a linearly interpolated empirical quantile."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("quantile requires at least one value")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be between zero and one")
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def balanced_accuracy(probabilities: list[float], outcomes: list[bool]) -> float | None:
    """Balanced direction accuracy, or None when only one class is observed."""
    positives = [i for i, outcome in enumerate(outcomes) if outcome]
    negatives = [i for i, outcome in enumerate(outcomes) if not outcome]
    if not positives or not negatives:
        return None
    sensitivity = sum(probabilities[i] >= 0.5 for i in positives) / len(positives)
    specificity = sum(probabilities[i] < 0.5 for i in negatives) / len(negatives)
    return (sensitivity + specificity) / 2.0


def fit_isotonic(probabilities: list[float], outcomes: list[bool]) -> dict:
    """Fit a beta-smoothed PAVA isotonic probability calibrator.

    The result is JSON-serializable and deliberately includes its sample size;
    callers must not claim calibration from an undersized walk-forward sample.
    """
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("probabilities and outcomes must have equal non-zero length")
    pairs = sorted((min(1.0, max(0.0, float(p))), float(y))
                   for p, y in zip(probabilities, outcomes))
    blocks: list[dict] = []
    def rate(block: dict) -> float:
        # Beta(2, 2) shrinkage prevents tiny calibration sets from emitting
        # false-certainty 0% and 100% probabilities.
        return (block["sum"] + 2.0) / (block["count"] + 4.0)

    for probability, outcome in pairs:
        blocks.append({
            "min": probability,
            "max": probability,
            "sum": outcome,
            "count": 1,
        })
        while len(blocks) >= 2:
            left, right = blocks[-2], blocks[-1]
            if rate(left) <= rate(right):
                break
            blocks[-2:] = [{
                "min": left["min"],
                "max": right["max"],
                "sum": left["sum"] + right["sum"],
                "count": left["count"] + right["count"],
            }]
    return {
        "method": "isotonic_pava_beta_smoothed",
        "sample_size": len(pairs),
        "knots": [block["max"] for block in blocks],
        "values": [rate(block) for block in blocks],
    }


def apply_isotonic(probability: float, calibrator: dict) -> float:
    """Apply a fitted stepwise isotonic calibrator."""
    knots = calibrator.get("knots") or []
    values = calibrator.get("values") or []
    if not knots or len(knots) != len(values):
        raise ValueError("invalid isotonic calibrator")
    index = min(bisect.bisect_left(knots, probability), len(values) - 1)
    return min(1.0, max(0.0, float(values[index])))


def calibration_error(probabilities: list[float], outcomes: list[bool],
                      bins: int = 5) -> float:
    """Expected calibration error using fixed-width probability bins."""
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("probabilities and outcomes must have equal non-zero length")
    total = len(probabilities)
    error = 0.0
    for bucket in range(bins):
        lo, hi = bucket / bins, (bucket + 1) / bins
        members = [i for i, p in enumerate(probabilities)
                   if lo <= p < hi or (bucket == bins - 1 and p == 1.0)]
        if members:
            confidence = sum(probabilities[i] for i in members) / len(members)
            observed = sum(outcomes[i] for i in members) / len(members)
            error += len(members) / total * abs(confidence - observed)
    return error
