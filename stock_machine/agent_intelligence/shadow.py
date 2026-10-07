"""Prospective signal evaluation. Never changes bandit state or paper instructions."""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from collections import defaultdict

from .. import db, research_store
from ..agents.contracts import digest
from ..market_calendar import (
    latest_completed_session,
    session_offset,
    session_dates,
    next_close_after,
)

VERSION = "agent-shadow.v1"
HORIZONS = (5, 10, 20)
SNAPSHOT = "AGENT_SHADOW_SNAPSHOT_V1"
OUTCOME = "AGENT_SHADOW_OUTCOME_V1"
WEIGHTS = "AGENT_SHADOW_WEIGHTS_V1"
CHECK = "AGENT_SHADOW_CHECK_V1"
PRIOR_STRENGTH = 32.0


def number(value, lo=None, hi=None):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
        and (lo is None or value >= lo)
        and (hi is None or value <= hi)
    )


def stamp(value):
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("SHADOW_TIMESTAMP_NOT_AWARE")
    return d.astimezone(timezone.utc)


def candidate_weights(history, components, ticker, horizon):
    """Inverse directional error, stock estimates shrunk to pooled history.

    Correlated forecast models share one family budget. These are candidates,
    not calibrated probabilities or estimates of independent sample size.
    """
    pooled, local = defaultdict(list), defaultdict(list)
    for row in history:
        if row["horizon_sessions"] != horizon:
            continue
        for name, value in row["component_errors"].items():
            if name in components:
                pooled[name].append(value)
                if row["ticker"] == ticker:
                    local[name].append(value)
    errors, counts = {}, {}
    for name in components:
        base = sum(pooled[name]) / len(pooled[name]) if pooled[name] else 1.0
        errors[name] = (sum(local[name]) + PRIOR_STRENGTH * base) / (
            len(local[name]) + PRIOR_STRENGTH
        )
        counts[name] = {"stock": len(local[name]), "pooled": len(pooled[name])}
    groups = defaultdict(list)
    for name in components:
        groups["forecast" if name.startswith("forecast:") else name].append(name)
    family_scores = {
        g: 1 / (0.25 + sum(errors[n] for n in names) / len(names))
        for g, names in groups.items()
    }
    total = sum(family_scores.values())
    weights = {}
    for g, names in groups.items():
        within = {n: 1 / (0.25 + errors[n]) for n in names}
        subtotal = sum(within.values())
        for n in names:
            weights[n] = family_scores[g] / total * within[n] / subtotal
    return {
        "weights": weights,
        "counts": counts,
        "prior_strength": PRIOR_STRENGTH,
        "method": "family-budget-inverse-directional-error.v1",
        "status": "COLD_START" if not any(pooled.values()) else "SHADOW_CANDIDATE",
        "promotion": "NOT_AUTHORIZED",
    }


def training_history(conn, now):
    # Only outcomes recorded before this snapshot are usable. SQL filtering
    # precedes LIMIT, avoiding oldest-history truncation and future leakage.
    rows = conn.execute(
        """SELECT payload FROM research_evidence_records
        WHERE kind=%s AND recorded_at<%s AND payload->>'due_session'<=%s
        ORDER BY recorded_at DESC,record_id DESC LIMIT 5000""",
        (OUTCOME, now, latest_completed_session(now)),
    ).fetchall()
    return [r[0] for r in rows]


def capture(conn, decision, packet, intelligence, *, now=None):
    now = now or datetime.now(timezone.utc)
    now = stamp(now)
    ticker, decision_id = decision["ticker"], decision["decision_id"]
    state = intelligence["state"]
    origin = decision.get("price_date")
    keys, withheld = [], []
    # No historical backfill: source origin must be the session known now.
    if origin != latest_completed_session(now):
        return {"status": "WITHHELD", "reason": "STALE_RESEARCH", "keys": []}
    base = {
        n: float(v)
        for n, v in (state.get("signal_components") or {}).items()
        if number(v, -1, 1)
    }
    if any(
        b in state.get("blockers", [])
        for b in ("DATA_QUALITY_NOT_PASS", "FINANCIAL_INTEGRITY_NOT_VERIFIED")
    ):
        base.pop("fundamental", None)
    model = packet.get("model_distribution") or {}
    forecast_valid = False
    try:
        forecast_valid = (
            model.get("as_of") == origin
            and stamp(model.get("generated_at")) <= now
            and bool(model.get("forecast_id"))
            and bool(model.get("model_version"))
        )
    except (ValueError, TypeError):
        pass
    company = db.fetch_company(conn, ticker) or {}
    history = training_history(conn, now)
    history_hash = digest(history)
    for horizon in HORIZONS:
        key = f"{decision_id}:{horizon}"
        existing = research_store.get(conn, SNAPSHOT, key)
        if existing:
            if (
                existing["payload"]["input_sha256"] != decision["input_sha256"]
                or existing["payload"]["ticker"] != ticker
            ):
                raise ValueError("SHADOW_REPLAY_IDENTITY_CONFLICT")
            keys.append(key)
            continue
        components, forecasts = dict(base), {}
        available_models = model.get("short_horizon_models") or {}
        for name in sorted(
            set(available_models) | {"bootstrap", "bootstrap_drift_neutral", "lstm"}
        ):
            summary = available_models.get(name) or {}
            matches = [
                r
                for r in (summary.get("horizons") or {}).values()
                if r.get("days") == horizon
            ]
            if not forecast_valid or len(matches) != 1:
                withheld.append(f"{name}:{horizon}:UNAVAILABLE_OR_STALE")
                continue
            row = matches[0]
            probability = row.get("prob_positive")
            if not number(probability, 0, 1):
                withheld.append(f"{name}:{horizon}:INVALID_PROBABILITY")
                continue
            component = f"forecast:{name}:{model.get('model_version')}"
            components[component] = 2 * float(probability) - 1
            forecasts[component] = {
                **row,
                "forecast_id": model["forecast_id"],
                "model_version": model.get("model_version"),
                "model_name": name,
                "origin_adjusted_price": model.get("forecast_origin_adjusted_price"),
            }
        if not components or not number(state.get("bias_score"), -1, 1):
            continue
        weights = candidate_weights(history, components, ticker, horizon)
        weight_payload = {
            **weights,
            "ticker": ticker,
            "horizon_sessions": horizon,
            "training_cutoff": now.isoformat(),
            "source_outcomes": len(history),
            "training_history_hash": history_hash,
            "training_window_limit": 5000,
            "feature_contract": VERSION,
            "sector": company.get("sector"),
        }
        weight_payload["weight_version"] = digest(weight_payload)
        research_store.save(conn, WEIGHTS, key, weight_payload, ticker)
        selected = ((intelligence.get("bandit") or {}).get("selected") or {}).get(
            "action"
        )
        snapshot = {
            "schema_version": VERSION,
            "ticker": ticker,
            "decision_id": decision_id,
            "horizon_sessions": horizon,
            "origin_session": origin,
            "due_session": session_offset(origin, horizon),
            "captured_at": now.isoformat(),
            "first_future_session": next_close_after(now.isoformat()),
            "input_sha256": decision["input_sha256"],
            "sector": company.get("sector"),
            "components": components,
            "forecasts": forecasts,
            "paper_eligible": state.get("paper_eligible"),
            "paper_blockers": state.get("blockers", []),
            "candidate_score": sum(
                weights["weights"][n] * v for n, v in components.items()
            ),
            "baseline_score": state["bias_score"],
            "baseline_direction": state.get("direction", "NEUTRAL"),
            "agent_action": selected,
            "weight_version": weight_payload["weight_version"],
            "training_cutoff": now.isoformat(),
            "broker_submission": False,
            "evaluation_basis": "direction-from-known-close; no simulated fill or P&L",
        }
        research_store.save(conn, SNAPSHOT, key, snapshot, ticker)
        keys.append(key)
    return {
        "status": "CAPTURED" if keys else "WITHHELD",
        "keys": keys,
        "forecast_withheld": sorted(set(withheld)),
        "promotion": "NOT_AUTHORIZED",
    }


def evaluate(snapshot, prices, *, completed):
    if snapshot["due_session"] > completed:
        raise ValueError("SHADOW_NOT_MATURE")
    origin, due = snapshot["origin_session"], snapshot["due_session"]
    if snapshot["first_future_session"] > due:
        raise ValueError("SHADOW_CAPTURE_AFTER_TARGET")
    by_date = {r["date"]: r.get("adj_close") for r in prices}
    if any(
        not number(by_date.get(day), 0.000000001) for day in session_dates(origin, due)
    ):
        raise ValueError("SHADOW_ADJUSTED_PATH_INCOMPLETE")
    ret = by_date[due] / by_date[origin] - 1
    target = 1.0 if ret > 0 else (-1.0 if ret < 0 else 0.0)

    def hit(score):
        return None if target == 0 or score == 0 else (score > 0) == (target > 0)

    forecast_scores = {}
    for name, row in snapshot["forecasts"].items():
        reference = row.get("origin_adjusted_price")
        quantiles = [row.get(n) for n in ("p10", "p50", "p90")]
        price_valid = (
            number(reference, 0.000000001)
            and all(number(v, 0.000000001) for v in quantiles)
            and quantiles == sorted(quantiles)
        )
        forecast_scores[name] = {
            "median_abs_return_error_pct": (
                abs(row["p50"] / reference - 1 - ret) * 100 if price_valid else None
            ),
            "interval_80_covered": (
                row["p10"] <= reference * (1 + ret) <= row["p90"]
                if price_valid
                else None
            ),
            "brier": (
                None if target == 0 else (row["prob_positive"] - (target > 0)) ** 2
            ),
            "direction_hit": hit(2 * row["prob_positive"] - 1),
            "calibration_status": row.get("calibration_status", "pending"),
        }
    return {
        "schema_version": VERSION,
        "status": "SCORED",
        "ticker": snapshot["ticker"],
        "decision_id": snapshot["decision_id"],
        "horizon_sessions": snapshot["horizon_sessions"],
        "origin_session": origin,
        "due_session": due,
        "realized_return_pct": ret * 100,
        "component_errors": {
            n: (v - target) ** 2 for n, v in snapshot["components"].items()
        },
        "candidate_error": (snapshot["candidate_score"] - target) ** 2,
        "baseline_error": (snapshot["baseline_score"] - target) ** 2,
        "candidate_hit": hit(
            snapshot["candidate_score"]
            if abs(snapshot["candidate_score"]) >= 0.25
            else 0
        ),
        "baseline_hit": hit(
            snapshot["baseline_score"] if abs(snapshot["baseline_score"]) >= 0.25 else 0
        ),
        "agent_action_hit": hit(
            {"LONG_STOCK": 1, "SHORT_STOCK": -1}.get(snapshot["agent_action"], 0)
        ),
        "forecast_scores": forecast_scores,
        "weight_version": snapshot["weight_version"],
        "price_vintage_hash": digest(
            {d: by_date[d] for d in session_dates(origin, due)}
        ),
        "broker_submission": False,
        "promotion": "NOT_AUTHORIZED",
    }


def score_matured(*, limit=100):
    if not 1 <= limit <= 1000:
        raise ValueError("SHADOW_LIMIT_INVALID")
    completed = latest_completed_session()
    results = []
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT r.request_key,r.payload FROM research_evidence_records r
            WHERE r.kind=%s AND r.payload->>'due_session'<=%s
              AND NOT EXISTS (SELECT 1 FROM research_evidence_records o
                WHERE o.kind=%s AND o.request_key=r.request_key)
              AND NOT EXISTS (SELECT 1 FROM research_evidence_records a
                WHERE a.kind=%s AND a.request_key=r.request_key || ':' || %s)
            ORDER BY r.payload->>'due_session',r.recorded_at,r.record_id LIMIT %s""",
            (SNAPSHOT, completed, OUTCOME, CHECK, completed, limit),
        ).fetchall()
    for key, snapshot in rows:
        try:
            with db.connect() as conn:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    ("shadow:" + key,),
                )
                if research_store.get(conn, OUTCOME, key):
                    continue
                outcome = evaluate(
                    snapshot,
                    db.fetch_prices(conn, snapshot["ticker"], completed),
                    completed=completed,
                )
                research_store.save(conn, OUTCOME, key, outcome, snapshot["ticker"])
                results.append({"key": key, "status": "SCORED"})
        except ValueError as exc:
            result = {"key": key, "status": "BLOCKED", "reason": str(exc)}
            with db.connect() as conn:
                research_store.save(
                    conn, CHECK, key + ":" + completed, result, snapshot["ticker"]
                )
            results.append(result)
    return {
        "status": (
            "ATTENTION" if any(r["status"] == "BLOCKED" for r in results) else "OK"
        ),
        "scored": sum(r["status"] == "SCORED" for r in results),
        "results": results,
        "broker_submission": False,
        "promotion": "NOT_AUTHORIZED",
    }


def metric_mean(metrics, field):
    values = [m[field] for m in metrics if m[field] is not None]
    return sum(values) / len(values) if values else None


def weekly_summary(conn, *, end=None):
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    end = end or latest_completed_session()
    start = session_offset(end, -5)
    rows = conn.execute(
        """SELECT payload FROM research_evidence_records
        WHERE kind=%s AND payload->>'due_session'>%s AND payload->>'due_session'<=%s
        ORDER BY recorded_at DESC,record_id DESC LIMIT 5001""",
        (OUTCOME, start, end),
    ).fetchall()
    if len(rows) > 5000:
        return {
            "status": "ATTENTION",
            "reason": "WEEKLY_REPORT_LIMIT_EXCEEDED",
            "rows": [],
        }
    weights = conn.execute(
        """SELECT DISTINCT ON (ticker,payload->>'horizon_sessions') payload
        FROM research_evidence_records WHERE kind=%s
        ORDER BY ticker,payload->>'horizon_sessions',recorded_at DESC,record_id DESC""",
        (WEIGHTS,),
    ).fetchall()
    by_key = {(w[0]["ticker"], w[0]["horizon_sessions"]): w[0] for w in weights}
    grouped = defaultdict(list)
    for (row,) in rows:
        grouped[(row["ticker"], row["horizon_sessions"])].append(row)
    summary = []
    for ticker, horizon in sorted(set(grouped) | set(by_key)):
        values = grouped[(ticker, horizon)]

        def average(field):
            return sum(v[field] for v in values) / len(values) if values else None

        def accuracy(field):
            valid = [v[field] for v in values if v[field] is not None]
            return sum(valid) / len(valid) if valid else None

        forecasts = defaultdict(list)
        for value in values:
            for name, metrics in value["forecast_scores"].items():
                forecasts[name].append(metrics)
        summary.append(
            {
                "ticker": ticker,
                "horizon_sessions": horizon,
                "matured": len(values),
                "candidate_error": average("candidate_error"),
                "baseline_error": average("baseline_error"),
                "candidate_hit_rate": accuracy("candidate_hit"),
                "candidate_direction_observations": sum(
                    v["candidate_hit"] is not None for v in values
                ),
                "baseline_direction_observations": sum(
                    v["baseline_hit"] is not None for v in values
                ),
                "baseline_hit_rate": accuracy("baseline_hit"),
                "agent_action_hit_rate": accuracy("agent_action_hit"),
                "agent_action_observations": sum(
                    v["agent_action_hit"] is not None for v in values
                ),
                "forecast_metrics": {
                    n: {
                        "brier": metric_mean(v, "brier"),
                        "observations": sum(m["brier"] is not None for m in v),
                        "price_observations": sum(
                            m["median_abs_return_error_pct"] is not None for m in v
                        ),
                        "median_abs_return_error_pct": metric_mean(
                            v, "median_abs_return_error_pct"
                        ),
                        "interval_80_coverage": metric_mean(v, "interval_80_covered"),
                    }
                    for n, v in forecasts.items()
                },
                "candidate_weights": by_key.get((ticker, horizon)),
            }
        )
    pending, overdue = conn.execute(
        """SELECT count(*),count(*) FILTER (WHERE r.payload->>'due_session'<=%s)
        FROM research_evidence_records r WHERE r.kind=%s
        AND NOT EXISTS (SELECT 1 FROM research_evidence_records o WHERE o.kind=%s AND o.request_key=r.request_key)""",
        (end, SNAPSHOT, OUTCOME),
    ).fetchone()
    return {
        "status": "ATTENTION" if overdue else ("OK" if rows else "AWAITING_MATURITY"),
        "period_start": start,
        "period_end": end,
        "matured": len(rows),
        "pending": pending,
        "due_pending": overdue,
        "rows": summary,
        "promotion": "NOT_AUTHORIZED",
        "broker_submission": False,
        "limitations": [
            "Directional comparison, not portfolio P&L or calibrated signal probabilities.",
            "Overlapping horizons and correlated stocks are not independent observations.",
            "Candidate weights are evaluated prospectively; no automatic promotion.",
        ],
    }
