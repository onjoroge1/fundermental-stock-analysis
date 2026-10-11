"""Pooled contextual model with sector offsets and a confidence gate, PAPER/SHADOW only.

One Bayesian linear model per trading action, shared by every covered stock,
plus a sector random intercept shrunk toward zero: a single ticker produces
about a dozen independent 20-session labels a year, too few for its own
offset, while a sector pools several tickers. NO_TRADE is a known zero
baseline. Rewards are stock-specific (beta-adjusted) net returns in ex-ante
volatility units (reward v5), so unit noise variance is the natural scale.

Every eligible decision is scored for both directions from its counterfactual
outcome (#101), so the model learns the same whatever is traded and
exploration buys no information. A trading arm is chosen only when the model
is at least 80% confident its net edge is positive; Thompson sampling, used
before, kept trading about half the time with no edge. Choices are
deterministic. No arm selection produced here can authorize broker execution.
"""

from __future__ import annotations

from math import isfinite, sqrt

VERSION = "pooled-linear-confident-sector.v3"
# Trade only when P(net edge > 0) >= 80%: z(0.80). Simulation (54 stocks, two
# years): no-edge losses fell ~80% versus Thompson sampling while keeping
# 85-90% of the reward when an edge exists.
CONFIDENCE_Z = 0.8416
# Sector intercept prior: sd 0.1 reward units. Per-ticker offsets were tested
# and not adopted: about 13 effective labels a ticker a year cannot learn them.
SECTOR_PRIOR_PRECISION = 100.0
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


def sector_offset(offsets_arm: dict | None, sector: str | None) -> tuple[float, float]:
    """Posterior mean and variance of a sector's intercept (0, 0 if unknown)."""
    if not sector:
        return 0.0, 0.0
    weight, weighted = ((offsets_arm or {}).get("sector") or {}).get(sector) or (0.0, 0.0)
    precision = float(weight) + SECTOR_PRIOR_PRECISION
    return float(weighted) / precision, NOISE_VARIANCE / precision


def predict(arm: dict, x: list[float], offsets_arm: dict | None = None, sector: str | None = None) -> dict:
    """Pooled linear estimate plus the shrunk sector intercept."""
    pooled = estimate(arm, x)
    offset, offset_variance = sector_offset(offsets_arm, sector)
    return {
        "mean": pooled["mean"] + offset,
        "uncertainty": sqrt(pooled["uncertainty"] ** 2 + offset_variance),
        "sector_offset": offset,
    }


def update_sector_offset(offsets_arm: dict | None, sector: str | None, residual: float, weight: float) -> dict:
    """Accumulate a weighted residual (label minus pooled estimate) for the sector."""
    out = {"sector": {k: list(v) for k, v in ((offsets_arm or {}).get("sector") or {}).items()}}
    if sector:
        w, wr = out["sector"].get(sector) or (0.0, 0.0)
        out["sector"][sector] = [float(w) + weight, float(wr) + weight * residual]
    return out


def select(
    state: dict,
    actions: list[str],
    arm_states: dict[str, dict] | None = None,
    *,
    mode: str = "PAPER",
    decision_key: str = "",
    offsets: dict | None = None,
) -> dict:
    """Confidence-gated choice; decision_key is kept for callers' audit records."""
    if mode not in {"PAPER", "SHADOW"}:
        raise ValueError("BANDIT_LIVE_MODE_FORBIDDEN")
    x = context_vector(state)
    arm_states, offsets = arm_states or {}, offsets or {}
    sector = state.get("sector")
    scored = []
    for action in sorted(actions):
        if action == BASELINE_ACTION:
            est, observations = {"mean": 0.0, "uncertainty": 0.0, "sector_offset": 0.0}, None
        else:
            arm = arm_states.get(action) or empty_arm(len(x))
            est = predict(arm, x, offsets.get(action), sector)
            observations = int(arm.get("observations") or 0)
        score = est["mean"] - CONFIDENCE_Z * est["uncertainty"]
        scored.append(
            {
                "action": action,
                **est,
                "score": score,
                # Legacy display keys: the confidence adjustment and gated score.
                "exploration_bonus": score - est["mean"],
                "ucb": score,
                "observations": observations,
                "baseline": action == BASELINE_ACTION,
            }
        )
    # Ties, and anything not confidently above zero, go to the baseline.
    selected = max(scored, key=lambda r: (r["score"], r["baseline"]))
    leader = max(scored, key=lambda r: (r["mean"], r["baseline"]))
    if not selected["baseline"]:
        choice_driver = "EXPLOIT_CONFIDENT"
    elif not leader["baseline"] and leader["mean"] > 0:
        choice_driver = "ABSTAIN_UNCERTAIN"
    else:
        choice_driver = "ABSTAIN_NO_EDGE"
    return {
        "schema_version": VERSION,
        "mode": mode,
        "selection": "posterior-confidence",
        "confidence_z": CONFIDENCE_Z,
        "pooling": "all covered stocks share one model per action, plus shrunk sector intercepts",
        "prior_precision": PRIOR_PRECISION,
        "sector_prior_precision": SECTOR_PRIOR_PRECISION,
        "noise_variance": NOISE_VARIANCE,
        "features": dict(zip(FEATURE_NAMES, x)),
        "sector": sector,
        "selected": selected,
        "arms": scored,
        "choice_driver": choice_driver,
        "exploitation_leader": leader["action"],
        "exploration_enabled": False,
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
