"""P1 regime challenger shadow contract."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from .regime_model import walk_forward

MODEL_ID = "regime-alpha.v1"
MIN_REGIME_OK_COVERAGE = 0.80
MIN_BREADTH_COVERAGE = 0.60


def coverage(observations: list[dict]) -> dict:
    total = len(observations)
    tickers = {row["ticker"] for row in observations}
    ok = 0
    qqq = sector = breadth = 0
    classifications = Counter()
    for row in observations:
        regime = row.get("regime") or {}
        if regime.get("status") == "OK":
            ok += 1
        features = regime.get("features") or {}
        qqq += bool(features.get("has_qqq"))
        sector += bool(features.get("has_sector"))
        breadth += bool(features.get("has_breadth"))
        classifications[regime.get("classification", "UNKNOWN")] += 1
    div = total or 1
    return {
        "observations": total,
        "tickers": len(tickers),
        "regime_ok_coverage": round(ok / div, 4),
        "qqq_coverage": round(qqq / div, 4),
        "sector_proxy_coverage": round(sector / div, 4),
        "breadth_coverage": round(breadth / div, 4),
        "classification_counts": dict(classifications),
    }


def evaluate(observations: list[dict]) -> dict:
    cov = coverage(observations)
    model = walk_forward(observations)
    data_gate = (
        cov["regime_ok_coverage"] >= MIN_REGIME_OK_COVERAGE
        and cov["breadth_coverage"] >= MIN_BREADTH_COVERAGE
    )
    model_gate = (
        model.get("status") == "OK"
        and bool(model.get("verdict", {}).get("regime_model_beats_p0_and_baseline"))
    )
    if not data_gate:
        decision = "PENDING_MORE_REGIME_HISTORY"
        reason = "Stored market/breadth history is below the P1 evaluation minimum."
    elif not model_gate:
        decision = "REJECT"
        reason = "Regime challenger did not beat P0 unified plus the strongest dumb baseline."
    else:
        decision = "ELIGIBLE_FOR_P1_PROMOTION_REVIEW"
        reason = "P1 data and out-of-sample challenger gates passed; no automatic promotion occurs."
    return {
        "model_id": MODEL_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage": cov,
        "model": model,
        "promotion": {
            "decision": decision,
            "data_gate": data_gate,
            "model_gate": model_gate,
            "deployed_as_primary": False,
            "minimum_regime_ok_coverage": MIN_REGIME_OK_COVERAGE,
            "minimum_breadth_coverage": MIN_BREADTH_COVERAGE,
            "reason": reason,
        },
    }
