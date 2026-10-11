"""Learned direction as a prospective challenger to the heuristic direction.

The heuristic (fixed component weights and a +/-0.25 bias threshold in
state.assemble) decides which direction the router offers. This module
freezes, at decision time, what the pooled model would choose instead, and
evaluates both on the same matured counterfactual rewards under a protocol
fixed in code. A protocol change produces a new hash and fresh evidence; old
snapshots never count toward a new protocol. A passing result authorizes an
independent review, never an automatic change to paper trading.
"""

from __future__ import annotations

import random
from functools import lru_cache
from math import sqrt

from ..agents.contracts import digest
from . import bandit

PROTOCOL = {
    "protocol_id": "learned-direction-vs-heuristic.v1",
    "reward_version": "risk-scaled-residual-paper-reward.v5",
    "incumbent": "state.assemble direction: BULLISH->LONG, BEARISH->SHORT, NEUTRAL->FLAT",
    "challenger": (
        "pooled model plus shrunk sector intercept, posterior mean frozen at decision "
        "time: LONG if mean(LONG) > max(0, mean(SHORT)); SHORT if mean(SHORT) > "
        "max(0, mean(LONG)); else FLAT"
    ),
    "unit": "paper-eligible decision with a matured prospective counterfactual record",
    "reward": "counterfactual reward v5 (stock-specific, beta-adjusted) of the chosen direction; FLAT scores 0",
    "statistic": "mean paired difference, challenger minus incumbent",
    "clustering": "20-session blocks of execution sessions counted from the epoch",
    "epoch_session": "2026-10-01",
    "block_sessions": 20,
    "minimum_blocks": 12,
    "bootstrap": "resample blocks with replacement",
    "bootstrap_seed": 20261010,
    "bootstrap_samples": 5000,
    "pass_criteria": [
        "mean paired difference > 0",
        "bootstrap lower 2.5% bound of the mean paired difference > 0",
        "challenger mean reward > 0",
    ],
    "exclusions": "none; every decision carrying this protocol hash counts",
    "qualification": "PASS_REQUIRES_INDEPENDENT_REVIEW; no automatic promotion",
    "limitations": [
        "Adjacent blocks share part of their 20-session outcome windows.",
        "Stocks in one block share market moves; blocks, not decisions, are the units.",
        "Counterfactual fills use fixed costs; borrow and slippage are not modelled.",
    ],
}
PROTOCOL_SHA256 = digest(PROTOCOL)
DIRECTIONS = {"LONG": "LONG_STOCK", "SHORT": "SHORT_STOCK"}


def incumbent(state: dict) -> str:
    return {"BULLISH": "LONG", "BEARISH": "SHORT"}.get(state.get("direction"), "FLAT")


def challenger(state: dict, arms: dict, *, model_sequence: int | None, offsets: dict | None = None) -> dict:
    """Frozen learned direction; uses only the pooled state known at decision time."""
    x = bandit.context_vector(state)
    estimates = {
        side: bandit.predict(
            arms.get(arm) or bandit.empty_arm(len(x)), x, (offsets or {}).get(arm), state.get("sector")
        )
        for side, arm in DIRECTIONS.items()
    }
    long, short = estimates["LONG"]["mean"], estimates["SHORT"]["mean"]
    if long > max(0.0, short):
        direction = "LONG"
    elif short > max(0.0, long):
        direction = "SHORT"
    else:
        direction = "FLAT"
    return {
        "protocol_id": PROTOCOL["protocol_id"],
        "protocol_sha256": PROTOCOL_SHA256,
        "incumbent": incumbent(state),
        "challenger": direction,
        "estimates": estimates,
        "model_sequence": model_sequence,
        "acts_on_paper": False,
    }


@lru_cache(maxsize=8192)
def _session_index(epoch: str, session: str) -> int:
    from ..market_calendar import session_dates

    return len(session_dates(epoch, session)) - 1


def _block(session: str, epoch: str, size: int) -> int:
    if session < epoch:
        raise ValueError("DIRECTION_SESSION_BEFORE_EPOCH")
    return _session_index(epoch, session) // size


# z(0.975) + z(0.80): the true difference a two-sided 95% test detects 80% of the time.
DETECTION_MULTIPLIER = 1.959964 + 0.841621


def detectable(block_values: list[float], minimum_blocks: int) -> dict:
    """Smallest true difference detectable with 80% power from the observed noise.

    Uses the spread of block-level values, the same units the test
    resamples. A NOT_SUPERIOR result says little about differences smaller
    than this.
    """
    n = len(block_values)
    if n < 2:
        return {"detectable_difference_80pct": None, "detectable_at_minimum_blocks": None}
    mean = sum(block_values) / n
    sd = sqrt(sum((v - mean) ** 2 for v in block_values) / (n - 1))
    return {
        "detectable_difference_80pct": DETECTION_MULTIPLIER * sd / sqrt(n),
        "detectable_at_minimum_blocks": DETECTION_MULTIPLIER * sd / sqrt(max(n, minimum_blocks)),
        "block_difference_sd": sd,
    }


def evaluate(rows: list[dict], protocol: dict = PROTOCOL) -> dict:
    """rows: {execution_session, incumbent, challenger, rewards: {LONG, SHORT}}."""
    def value(direction, rewards):
        return 0.0 if direction == "FLAT" else float(rewards[direction])

    blocks: dict[int, list[tuple[float, float, float]]] = {}
    for row in rows:
        rewards = row["rewards"]
        inc = value(row["incumbent"], rewards)
        ch = value(row["challenger"], rewards)
        key = _block(row["execution_session"], protocol["epoch_session"], protocol["block_sessions"])
        blocks.setdefault(key, []).append((ch - inc, ch, inc))
    decisions = sum(len(v) for v in blocks.values())
    agreement = (
        sum(r["incumbent"] == r["challenger"] for r in rows) / len(rows) if rows else None
    )
    base = {
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": digest(protocol),
        "decisions": decisions,
        "blocks": len(blocks),
        "required_blocks": protocol["minimum_blocks"],
        "direction_agreement": agreement,
        "promotion": "NOT_AUTHORIZED",
        "trade_qualification": False,
    }
    if not blocks:
        return {**base, "status": "AWAITING_MATURED_DECISIONS"}
    # Blocks, not decisions, are the resampling units.
    means = [
        tuple(sum(v[i] for v in values) / len(values) for i in range(3))
        for _, values in sorted(blocks.items())
    ]
    diff = sum(m[0] for m in means) / len(means)
    ch_mean = sum(m[1] for m in means) / len(means)
    inc_mean = sum(m[2] for m in means) / len(means)
    summary = {
        **base,
        "mean_paired_difference": diff,
        "challenger_mean_reward": ch_mean,
        "incumbent_mean_reward": inc_mean,
        **detectable([m[0] for m in means], protocol["minimum_blocks"]),
    }
    if len(blocks) < protocol["minimum_blocks"]:
        return {**summary, "status": "PENDING_EVIDENCE"}
    rng = random.Random(protocol["bootstrap_seed"])
    diffs = [m[0] for m in means]
    draws = sorted(
        sum(rng.choice(diffs) for _ in diffs) / len(diffs)
        for _ in range(protocol["bootstrap_samples"])
    )
    lower = draws[int(0.025 * len(draws))]
    passed = diff > 0 and lower > 0 and ch_mean > 0
    return {
        **summary,
        "lower_95pct_paired_difference": lower,
        "status": "PASS_REQUIRES_INDEPENDENT_REVIEW" if passed else "NOT_SUPERIOR",
    }


def matured_rows(conn) -> list[dict]:
    """Decisions frozen under this protocol whose counterfactual outcome is recorded."""
    rows = conn.execute(
        """SELECT i.payload #>> '{learning,execution_session}',
                  i.payload #>> '{direction_challenger,incumbent}',
                  i.payload #>> '{direction_challenger,challenger}',
                  r.payload #>> '{arms,LONG_STOCK,reward,reward}',
                  r.payload #>> '{arms,SHORT_STOCK,reward,reward}'
        FROM research_evidence_records i
        JOIN research_evidence_records r
          ON r.kind='AGENT_REWARD_V3' AND r.request_key=i.request_key
        WHERE i.kind='AGENT_INTELLIGENCE_V2'
          AND i.payload #>> '{direction_challenger,protocol_sha256}'=%s
          AND r.payload->>'learning_basis'='PROSPECTIVE_COUNTERFACTUAL_V1'
          AND r.payload #>> '{arms,LONG_STOCK,reward,schema_version}'=%s""",
        (PROTOCOL_SHA256, PROTOCOL["reward_version"]),
    ).fetchall()
    return [
        {
            "execution_session": session,
            "incumbent": inc,
            "challenger": ch,
            "rewards": {"LONG": float(long), "SHORT": float(short)},
        }
        for session, inc, ch, long, short in rows
    ]


def summary(conn) -> dict:
    return evaluate(matured_rows(conn))
