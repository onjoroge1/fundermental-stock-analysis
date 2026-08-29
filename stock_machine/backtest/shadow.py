"""P0-C full-universe shadow evaluation and promotion decision.

This layer turns the unified model into a measured research artifact without
changing production rankings. It records panel coverage, out-of-sample model
results, and a fail-closed promotion verdict.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from .unified_model import walk_forward

MODEL_ID = "unified-alpha.v1"
MIN_EXPECTATIONS_COVERAGE = 0.60
MIN_EXPECTATIONS_DATES = 8


def panel_coverage(observations: list[dict]) -> dict:
    total = len(observations)
    with_any = 0
    with_revision = 0
    expectation_dates: set[str] = set()
    names: set[str] = set()
    missing_counts: Counter[str] = Counter()

    keys = (
        "eps_revision_pct",
        "revenue_revision_pct",
        "latest_eps_surprise_pct",
        "trailing_4q_eps_surprise_pct",
    )
    for row in observations:
        names.add(row["ticker"])
        ex = row.get("expectations") or {}
        available = [k for k in keys if ex.get(k) is not None]
        if available:
            with_any += 1
            expectation_dates.add(row["as_of"])
        if ex.get("eps_revision_pct") is not None or ex.get("revenue_revision_pct") is not None:
            with_revision += 1
        for key in keys:
            if ex.get(key) is None:
                missing_counts[key] += 1

    return {
        "observations": total,
        "tickers": len(names),
        "expectations_observations": with_any,
        "revision_observations": with_revision,
        "expectations_coverage": round(with_any / total, 4) if total else 0.0,
        "revision_coverage": round(with_revision / total, 4) if total else 0.0,
        "expectations_dates": len(expectation_dates),
        "missing_by_feature": dict(missing_counts),
    }


def evaluate_shadow(observations: list[dict]) -> dict:
    coverage = panel_coverage(observations)
    model = walk_forward(observations)

    coverage_gate = (
        coverage["expectations_coverage"] >= MIN_EXPECTATIONS_COVERAGE
        and coverage["expectations_dates"] >= MIN_EXPECTATIONS_DATES
    )
    model_gate = (
        model.get("status") == "OK"
        and bool(model.get("verdict", {}).get("model_beats_baseline"))
    )

    if not coverage_gate:
        decision = "PENDING_MORE_POINT_IN_TIME_EXPECTATIONS_HISTORY"
        reason = (
            "Real consensus-vintage coverage is below the promotion minimum; "
            "missing historical revisions are not backfilled or inferred."
        )
    elif not model_gate:
        decision = "REJECT"
        reason = "Unified model did not beat the strongest dumb baseline out of sample."
    else:
        decision = "ELIGIBLE_FOR_PROMOTION_REVIEW"
        reason = (
            "Coverage and out-of-sample baseline gates passed; production promotion "
            "still requires an explicit code change and review."
        )

    return {
        "model_id": MODEL_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage": coverage,
        "model": model,
        "promotion": {
            "decision": decision,
            "coverage_gate": coverage_gate,
            "model_gate": model_gate,
            "deployed_as_primary": False,
            "minimum_expectations_coverage": MIN_EXPECTATIONS_COVERAGE,
            "minimum_expectations_dates": MIN_EXPECTATIONS_DATES,
            "reason": reason,
        },
    }
