"""Pooled contextual Thompson sampling for PAPER/SHADOW only.

One Bayesian linear model per trading action, shared by every covered stock:
a single ticker produces about a dozen non-overlapping 20-session trades a
year, too few to learn from alone. NO_TRADE is a known zero-reward baseline,
not an arm to estimate. Rewards are in ex-ante volatility units (reward v4),
so unit noise variance is the natural scale. Sampling is seeded by the
decision key, making every choice reproducible for audit. No arm selection
produced here can authorize broker execution.
"""

from __future__ import annotations

import hashlib
import random
from math import isfinite, sqrt

VERSION = "pooled-linear-thompson.v2"
SIGNALS = ("fundamental", "technical", "news", "regime")
FEATURE_NAMES = (
    "intercept",
    *SIGNALS,
    "volatility_z",
    *(name + "_missing" for name in (*SIGNALS, "volatility")),
)
BASELINE_ACTION = "NO_TRADE"
PRIOR_PRECISION = 1.0
NOISE_VARIANCE = 1.0
# Standardization for annualized 20-session volatility; typical covered
# stocks sit near 35%. Clipped so one extreme name cannot dominate.
VOL_CENTER, VOL_SCALE, VOL_CLIP = 0.35, 0.25, (-2.0, 3.0)


def _present(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
    )


def context_vector(state: dict) -> list[float]:
    """Signals plus explicit missing flags.

    v1 coerced a missing signal to 0, indistinguishable from a genuinely
    neutral reading, and fed raw volatility clipped to [0, 1], which acted
    as a second intercept. The composite bias is a weighted sum of the
    signals, so it is not a feature.
    """
    parts = state.get("signal_components") or {}
    tech = (state.get("technical") or {}).get("features") or {}
    values = [parts.get(name) for name in SIGNALS]
    vol = tech.get("realized_vol_20")
    vol_ok = _present(vol) and vol > 0
    vol_z = (
        min(VOL_CLIP[1], max(VOL_CLIP[0], (float(vol) - VOL_CENTER) / VOL_SCALE))
        if vol_ok
        else 0.0
    )
    return [
        1.0,
        *(float(v) if _present(v) else 0.0 for v in values),
        vol_z,
        *(0.0 if _present(v) else 1.0 for v in values),
        0.0 if vol_ok else 1.0,
    ]


def empty_arm(width: int | None = None) -> dict:
    width = width or len(FEATURE_NAMES)
    return {
        "precision": [
            [PRIOR_PRECISION if i == j else 0.0 for j in range(width)]
            for i in range(width)
        ],
        "b": [0.0] * width,
        "observations": 0,
        "reward_sum": 0.0,
    }


def _checked(arm: dict, width: int) -> tuple[list[list[float]], list[float]]:
    a, b = arm.get("precision"), arm.get("b")
    if (
        not isinstance(a, list)
        or not isinstance(b, list)
        or len(a) != width
        or len(b) != width
        or any(not isinstance(row, list) or len(row) != width for row in a)
    ):
        raise ValueError("BANDIT_STATE_WIDTH_MISMATCH")
    return a, b


def _cholesky(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    low = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = a[i][j] - sum(low[i][k] * low[j][k] for k in range(j))
            if i == j:
                if s <= 0:
                    raise ValueError("BANDIT_PRECISION_NOT_POSITIVE_DEFINITE")
                low[i][j] = sqrt(s)
            else:
                low[i][j] = s / low[j][j]
    return low


def _solve(low: list[list[float]], v: list[float]) -> list[float]:
    n = len(low)
    y = [0.0] * n
    for i in range(n):
        y[i] = (v[i] - sum(low[i][k] * y[k] for k in range(i))) / low[i][i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (y[i] - sum(low[k][i] * x[k] for k in range(i + 1, n))) / low[i][i]
    return x


def estimate(arm: dict, x: list[float]) -> dict:
    """Posterior mean and standard deviation of the expected reward at x."""
    a, b = _checked(arm, len(x))
    low = _cholesky(a)
    theta = _solve(low, b)
    mean = sum(t * v for t, v in zip(theta, x))
    variance = NOISE_VARIANCE * sum(v * w for v, w in zip(x, _solve(low, x)))
    return {"mean": mean, "uncertainty": sqrt(max(variance, 0.0))}


def _rng(decision_key: str) -> random.Random:
    seed = hashlib.sha256((VERSION + ":" + decision_key).encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


def select(
    state: dict,
    actions: list[str],
    arm_states: dict[str, dict] | None = None,
    *,
    mode: str = "PAPER",
    decision_key: str = "",
) -> dict:
    if mode not in {"PAPER", "SHADOW"}:
        raise ValueError("BANDIT_LIVE_MODE_FORBIDDEN")
    x = context_vector(state)
    arm_states = arm_states or {}
    rng = _rng(decision_key)
    scored = []
    # Sorted order fixes the random draw sequence for a given decision key.
    for action in sorted(actions):
        if action == BASELINE_ACTION:
            est, sample, observations = {"mean": 0.0, "uncertainty": 0.0}, 0.0, None
        else:
            arm = arm_states.get(action) or empty_arm(len(x))
            est = estimate(arm, x)
            # Exact for the scalar projection x.theta of the Gaussian posterior.
            sample = rng.gauss(est["mean"], est["uncertainty"])
            observations = int(arm.get("observations") or 0)
        scored.append(
            {
                "action": action,
                **est,
                "sample": sample,
                "exploration_bonus": sample - est["mean"],
                # Legacy display key: the score the selection maximized.
                "ucb": sample,
                "observations": observations,
                "baseline": action == BASELINE_ACTION,
            }
        )
    # Ties go to the capital-preserving baseline.
    selected = max(scored, key=lambda r: (r["sample"], r["baseline"]))
    leader = max(scored, key=lambda r: (r["mean"], r["baseline"]))
    if not selected["baseline"] and selected["observations"] == 0:
        choice_driver = "EXPLORE_UNTRIED"
    elif selected["action"] != leader["action"]:
        choice_driver = "EXPLORE_UNCERTAINTY"
    else:
        choice_driver = "EXPLOIT_ESTIMATE"
    return {
        "schema_version": VERSION,
        "mode": mode,
        "sampling": "thompson",
        "pooling": "all covered stocks share one model per action",
        "prior_precision": PRIOR_PRECISION,
        "noise_variance": NOISE_VARIANCE,
        "features": dict(zip(FEATURE_NAMES, x)),
        "selected": selected,
        "arms": scored,
        "choice_driver": choice_driver,
        "exploitation_leader": leader["action"],
        "exploration_enabled": True,
        "broker_submission": False,
    }


def update(arm: dict, x: list[float], reward: float, weight: float = 1.0) -> dict:
    """Weighted Bayesian linear update.

    weight < 1 discounts observations that overlap others: daily decisions on
    one stock share most of a 20-session outcome window, so each carries
    about 1/20 of an independent observation's information.
    """
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 < weight <= 1:
        raise ValueError("BANDIT_WEIGHT_INVALID")
    a, b = _checked(arm, len(x))
    w = float(weight)
    return {
        "precision": [
            [a[i][j] + w * x[i] * x[j] for j in range(len(x))] for i in range(len(x))
        ],
        "b": [b[i] + w * reward * x[i] for i in range(len(x))],
        "observations": int(arm.get("observations") or 0) + 1,
        "effective_observations": float(arm.get("effective_observations") or 0.0) + w,
        "reward_sum": float(arm.get("reward_sum") or 0.0) + float(reward),
    }
