"""Prospective signal evaluation. Never changes bandit state or paper instructions."""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite, sqrt
from collections import defaultdict

from .. import db, research_store
from ..agents.contracts import digest
from ..regime import sector_etf
from ..market_calendar import (
    latest_completed_session,
    session_offset,
    session_dates,
    next_close_after,
)

VERSION = "agent-shadow.v2"
# Entry-aligned: signals are judged from the close a paper fill would use,
# not from the research origin close a day earlier.
TARGET = "spy-beta-residual-entry.v1"
HORIZONS = (5, 10, 20)
SNAPSHOT = "AGENT_SHADOW_SNAPSHOT_V1"
OUTCOME = "AGENT_SHADOW_OUTCOME_V1"
WEIGHTS = "AGENT_SHADOW_WEIGHTS_V1"
CHECK = "AGENT_SHADOW_CHECK_V1"
PRIOR_STRENGTH = 32.0
TRAINING_WINDOW_SESSIONS = 252
# About 162 outcomes a session at full coverage fill the window with ~41,000;
# reaching the cap means the window was cut short, which is recorded.
TRAINING_ROW_CAP = 60000
# The regime signal is a market-timing call: scored against SPY and kept out
# of the stock-specific candidate score.
MARKET_COMPONENTS = frozenset({"regime"})
BETA_RANGE = (0.0, 3.0)
DEFAULT_BETA = 1.0
DEFAULT_ANNUAL_VOL = 0.40
MIN_ANNUAL_VOL = 0.05


WEIGHTS_PROTOCOL = {
    "protocol_id": "shadow-candidate-weights-vs-heuristic.v1",
    "incumbent": "state.assemble bias score (fixed 0.50/0.25/0.15/0.10 weights)",
    "challenger": "candidate score from shadow weights frozen at capture",
    "weights_method": "family-budget-shrunk-information-coefficient.v2",
    "training_window_sessions": 252,
    "target": "spy-beta-residual-entry.v1",
    "statistic": "candidate IC minus incumbent IC against the volatility-scaled residual",
    "primary_horizon_sessions": 20,
    "secondary_horizons": "5 and 10 sessions: reported, never used for promotion",
    "clustering": "blocks of horizon-length origin sessions",
    "minimum_blocks": 12,
    "bootstrap_samples": 5000,
    "bootstrap_seed": 20261012,
    "pass_criteria": [
        "IC difference > 0",
        "bootstrap lower 2.5% bound of the IC difference > 0",
        "candidate IC > 0",
    ],
    "exclusions": "none; every outcome whose snapshot carries this protocol hash counts",
    "qualification": "PASS_REQUIRES_INDEPENDENT_REVIEW; no automatic promotion",
}
WEIGHTS_PROTOCOL_SHA256 = digest(WEIGHTS_PROTOCOL)


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


def _ic(pairs):
    """Uncentered correlation of signal with target: direction-sensitive, scale-free.

    A signal that is always zero, or a target that never moves, has no skill.
    """
    svz = sum(v * z for v, z in pairs)
    svv = sum(v * v for v, _ in pairs)
    szz = sum(z * z for _, z in pairs)
    return svz / sqrt(svv * szz) if svv > 0 and szz > 0 else 0.0


def candidate_weights(history, components, ticker, horizon):
    """Shrunk information coefficient against stock-specific returns.

    Squared error against a +/-1 direction (v1) rewarded timid signals: a
    zero-skill signal near 0 outscored a skilled confident one. The IC is
    independent of signal amplitude. Stock estimates shrink toward pooled
    history; components without positive skill get no weight; correlated
    forecast models share one family budget. These are candidates, not
    calibrated probabilities or estimates of independent sample size.
    """
    names = [n for n in components if n not in MARKET_COMPONENTS]
    pooled, local = defaultdict(list), defaultdict(list)
    for row in history:
        if row.get("horizon_sessions") != horizon or row.get("target") != TARGET:
            continue
        z = row.get("residual_z")
        if not number(z):
            continue
        for name, value in (row.get("component_values") or {}).items():
            if name in names and number(value):
                pooled[name].append((value, z))
                if row.get("ticker") == ticker:
                    local[name].append((value, z))
    skill, counts = {}, {}
    for name in names:
        n = len(local[name])
        skill[name] = (n * _ic(local[name]) + PRIOR_STRENGTH * _ic(pooled[name])) / (
            n + PRIOR_STRENGTH
        )
        counts[name] = {"stock": n, "pooled": len(pooled[name])}
    groups = defaultdict(list)
    for name in names:
        groups["forecast" if name.startswith("forecast:") else name].append(name)
    cold = not any(pooled[n] for n in names)
    if cold:
        family = {g: 1.0 for g in groups}
        within = {n: 1.0 for n in names}
    else:
        family = {
            g: max(0.0, sum(skill[n] for n in members) / len(members))
            for g, members in groups.items()
        }
        within = {n: max(0.0, skill[n]) for n in names}
    total = sum(family.values())
    weights = {n: 0.0 for n in components}
    for g, members in groups.items():
        subtotal = sum(within[n] for n in members)
        if total > 0 and subtotal > 0:
            for n in members:
                weights[n] = family[g] / total * within[n] / subtotal
    status = (
        "COLD_START" if cold else "SHADOW_CANDIDATE" if total > 0 else "NO_POSITIVE_SKILL"
    )
    return {
        "weights": weights,
        "skill": skill,
        "counts": counts,
        "prior_strength": PRIOR_STRENGTH,
        "method": "family-budget-shrunk-information-coefficient.v2",
        "target": TARGET,
        "market_components_excluded": sorted(set(components) & MARKET_COMPONENTS),
        "status": status,
        "promotion": "NOT_AUTHORIZED",
    }


def training_history(conn, now):
    """Outcomes whose targets completed within the last TRAINING_WINDOW_SESSIONS.

    Only outcomes recorded before this snapshot are usable; SQL filtering
    precedes LIMIT, avoiding truncation of old history and future leakage.
    The window is explicit in sessions. The latest 5,000 outcomes it replaced
    covered only about 31 sessions at full coverage. Only the fields training
    needs are read.
    """
    completed = latest_completed_session(now)
    start = session_offset(completed, -TRAINING_WINDOW_SESSIONS)
    rows = conn.execute(
        """SELECT jsonb_strip_nulls(jsonb_build_object(
                'ticker',payload->'ticker','horizon_sessions',payload->'horizon_sessions',
                'target',payload->'target','due_session',payload->'due_session',
                'component_values',payload->'component_values','residual_z',payload->'residual_z'))
        FROM research_evidence_records
        WHERE kind=%s AND recorded_at<%s AND payload->>'due_session'<=%s
          AND payload->>'due_session'>%s AND payload->>'target'=%s
        ORDER BY recorded_at DESC,record_id DESC LIMIT %s""",
        (OUTCOME, now, completed, start, TARGET, TRAINING_ROW_CAP),
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
    # The paper fill session frozen with the decision (#101), else the next close.
    entry = (intelligence.get("learning") or {}).get("execution_session")
    entry_basis = "paper_execution_session"
    if not entry:
        entry, entry_basis = next_close_after(now.isoformat()), "next_close_after_capture"
    features = (state.get("technical") or {}).get("features") or {}
    beta = features.get("beta_63_vs_spy")
    vol = features.get("realized_vol_20")
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
            "training_window_sessions": TRAINING_WINDOW_SESSIONS,
            "training_row_cap": TRAINING_ROW_CAP,
            "training_window_truncated": len(history) >= TRAINING_ROW_CAP,
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
            "forecast_due_session": session_offset(origin, horizon),
            "entry_session": entry,
            "entry_basis": entry_basis,
            # Scorable once both the forecast and the tradeable window mature.
            "due_session": max(session_offset(origin, horizon), session_offset(entry, horizon)),
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
            "weights_protocol_sha256": WEIGHTS_PROTOCOL_SHA256,
            "training_cutoff": now.isoformat(),
            "broker_submission": False,
            "evaluation_basis": "direction-from-known-close; no simulated fill or P&L",
            # Frozen at capture: the outcome target never uses later estimates.
            "target": TARGET,
            # Diagnostic benchmark: the sector ETF known at capture.
            "sector_etf": sector_etf(company.get("sector")),
            "beta": float(beta) if number(beta) else None,
            "ex_ante_vol": float(vol) if number(vol, 0) and vol > 0 else None,
        }
        research_store.save(conn, SNAPSHOT, key, snapshot, ticker)
        keys.append(key)
    return {
        "status": "CAPTURED" if keys else "WITHHELD",
        "keys": keys,
        "forecast_withheld": sorted(set(withheld)),
        "promotion": "NOT_AUTHORIZED",
    }


def _path(prices, origin, due, error):
    by_date = {r["date"]: r.get("adj_close") for r in prices}
    if any(
        not number(by_date.get(day), 0.000000001) for day in session_dates(origin, due)
    ):
        raise ValueError(error)
    return by_date


def windows(snapshot):
    """Forecast window (origin close) and tradeable window (paper entry close).

    Snapshots captured before entry alignment derive the entry from their
    frozen first_future_session.
    """
    horizon = snapshot["horizon_sessions"]
    origin = snapshot["origin_session"]
    forecast_due = snapshot.get("forecast_due_session") or session_offset(origin, horizon)
    entry = snapshot.get("entry_session") or snapshot["first_future_session"]
    return origin, forecast_due, entry, session_offset(entry, horizon)


def _sector_relative(snapshot, sector_prices, entry, due, ret, scale):
    """Stock return minus its sector ETF's over the tradeable window (diagnostic).

    Separates within-sector stock selection from sector rotation; never
    blocks scoring when the ETF or its path is missing.
    """
    if not snapshot.get("sector_etf") or not sector_prices:
        return {"sector_relative_return_pct": None, "sector_relative_z": None,
                "sector_relative_basis": "UNAVAILABLE"}
    try:
        path = _path(sector_prices, entry, due, "SECTOR_PATH_INCOMPLETE")
    except ValueError:
        return {"sector_relative_return_pct": None, "sector_relative_z": None,
                "sector_relative_basis": "SECTOR_PATH_INCOMPLETE"}
    relative = ret - (path[due] / path[entry] - 1)
    return {"sector_relative_return_pct": relative * 100, "sector_relative_z": relative / scale,
            "sector_relative_basis": snapshot["sector_etf"]}


def evaluate(snapshot, prices, *, completed, market_prices, sector_prices=None):
    origin, forecast_due, entry, due = windows(snapshot)
    if max(forecast_due, due) > completed:
        raise ValueError("SHADOW_NOT_MATURE")
    if snapshot["first_future_session"] > forecast_due:
        raise ValueError("SHADOW_CAPTURE_AFTER_TARGET")
    last = max(forecast_due, due)
    by_date = _path(prices, origin, last, "SHADOW_ADJUSTED_PATH_INCOMPLETE")
    market = _path(market_prices, entry, due, "SHADOW_MARKET_PATH_INCOMPLETE")
    # Signals and the agent's action: entry close to entry + horizon, the
    # window a paper position actually holds.
    ret = by_date[due] / by_date[entry] - 1
    market_ret = market[due] / market[entry] - 1
    # Forecast models predict from their origin close; keep their window.
    forecast_ret = by_date[forecast_due] / by_date[origin] - 1
    beta, beta_basis = snapshot.get("beta"), "frozen_beta_63_vs_spy"
    if not number(beta):
        beta, beta_basis = DEFAULT_BETA, "default_beta"
    beta = min(BETA_RANGE[1], max(BETA_RANGE[0], float(beta)))
    vol = snapshot.get("ex_ante_vol")
    vol = max(MIN_ANNUAL_VOL, float(vol)) if number(vol, 0) and vol > 0 else DEFAULT_ANNUAL_VOL
    residual = ret - beta * market_ret
    # Per-stock volatility scaling keeps volatile names from dominating skill.
    scale = vol * sqrt(snapshot["horizon_sessions"] / 252)
    residual_z = residual / scale

    def sign(x):
        return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)

    raw_target, residual_target = sign(ret), sign(residual)
    forecast_target = sign(forecast_ret)

    def hit(score, target):
        return None if target == 0 or score == 0 else (score > 0) == (target > 0)

    def thresholded(score):
        return score if abs(score) >= 0.25 else 0

    forecast_scores = {}
    for name, row in snapshot["forecasts"].items():
        # Forecast models predict raw stock prices; their own metrics stay raw.
        reference = row.get("origin_adjusted_price")
        quantiles = [row.get(n) for n in ("p10", "p50", "p90")]
        price_valid = (
            number(reference, 0.000000001)
            and all(number(v, 0.000000001) for v in quantiles)
            and quantiles == sorted(quantiles)
        )
        forecast_scores[name] = {
            "median_abs_return_error_pct": (
                abs(row["p50"] / reference - 1 - forecast_ret) * 100 if price_valid else None
            ),
            "interval_80_covered": (
                row["p10"] <= reference * (1 + forecast_ret) <= row["p90"]
                if price_valid
                else None
            ),
            "brier": (
                None if forecast_target == 0 else (row["prob_positive"] - (forecast_target > 0)) ** 2
            ),
            "direction_hit": hit(2 * row["prob_positive"] - 1, forecast_target),
            "calibration_status": row.get("calibration_status", "pending"),
        }
    return {
        "schema_version": VERSION,
        "target": TARGET,
        "status": "SCORED",
        "ticker": snapshot["ticker"],
        "decision_id": snapshot["decision_id"],
        "horizon_sessions": snapshot["horizon_sessions"],
        "origin_session": origin,
        "entry_session": entry,
        "due_session": due,
        "forecast_due_session": forecast_due,
        "realized_return_pct": ret * 100,
        "forecast_return_pct": forecast_ret * 100,
        "market_return_pct": market_ret * 100,
        "beta": beta,
        "beta_basis": beta_basis,
        "residual_return_pct": residual * 100,
        "residual_z": residual_z,
        **_sector_relative(snapshot, sector_prices, entry, due, ret, scale),
        "component_values": dict(snapshot["components"]),
        "candidate_score": snapshot["candidate_score"],
        "baseline_score": snapshot["baseline_score"],
        # Errors and hits judge the stock-specific move; the agent's action
        # is a raw stock position and is judged on the raw move.
        "candidate_error": (snapshot["candidate_score"] - residual_target) ** 2,
        "baseline_error": (snapshot["baseline_score"] - residual_target) ** 2,
        "candidate_hit": hit(thresholded(snapshot["candidate_score"]), residual_target),
        "baseline_hit": hit(thresholded(snapshot["baseline_score"]), residual_target),
        "agent_action_hit": hit(
            {"LONG_STOCK": 1, "SHORT_STOCK": -1}.get(snapshot["agent_action"], 0),
            raw_target,
        ),
        "market_component_hits": {
            n: hit(v, sign(market_ret))
            for n, v in snapshot["components"].items()
            if n in MARKET_COMPONENTS
        },
        "forecast_scores": forecast_scores,
        "weight_version": snapshot["weight_version"],
        "weights_protocol_sha256": snapshot.get("weights_protocol_sha256"),
        "price_vintage_hash": digest(
            {
                "stock": {d: by_date[d] for d in session_dates(origin, last)},
                "SPY": {d: market[d] for d in session_dates(entry, due)},
            }
        ),
        "broker_submission": False,
        "promotion": "NOT_AUTHORIZED",
    }


def score_matured(*, limit=100):
    if not 1 <= limit <= 1000:
        raise ValueError("SHADOW_LIMIT_INVALID")
    completed = latest_completed_session()
    results = []
    # One connection per pass, one transaction per snapshot.
    with db.connect() as conn:
        conn.autocommit = True
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
        market, sectors = None, {}
        for key, snapshot in rows:
            try:
                with conn.transaction():
                    conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        ("shadow:" + key,),
                    )
                    if research_store.get(conn, OUTCOME, key):
                        continue
                    if market is None:
                        # SPY is shared by every snapshot in the pass.
                        market = db.fetch_prices(conn, "SPY", completed)
                    etf = snapshot.get("sector_etf")
                    if etf and etf not in sectors:
                        sectors[etf] = db.fetch_prices(conn, etf, completed)
                    outcome = evaluate(
                        snapshot,
                        db.fetch_prices(conn, snapshot["ticker"], completed),
                        completed=completed,
                        market_prices=market,
                        sector_prices=sectors.get(etf) if etf else None,
                    )
                    research_store.save(conn, OUTCOME, key, outcome, snapshot["ticker"])
                results.append({"key": key, "status": "SCORED"})
            except ValueError as exc:
                if str(exc) == "SHADOW_NOT_MATURE":
                    # Pre-alignment snapshot whose entry window ends after its
                    # stored due session; it becomes scorable next session.
                    results.append({"key": key, "status": "PENDING_ENTRY_WINDOW"})
                    continue
                result = {"key": key, "status": "BLOCKED", "reason": str(exc)}
                with conn.transaction():
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


STATS_EPOCH = "2026-10-01"
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20261011


def _block_of(origin, horizon, index):
    # Blocks of `horizon` origin sessions: outcomes in one block share market
    # moves and overlapping windows, so blocks are the resampling units.
    return index.get(origin, 0) // max(1, int(horizon))


REFERENCE_WINDOW_SESSIONS = 252
REFERENCE_MINIMUM_OUTCOMES = 50


def _forecast_due(row):
    return row.get("forecast_due_session") or row.get("due_session")


def reference_counts(rows) -> dict:
    """Compact up/total counts per horizon and forecast-target session."""
    counts: dict = {}
    for r in rows:
        due, ret = _forecast_due(r), r.get("forecast_return_pct", r.get("realized_return_pct"))
        if due and number(ret) and ret != 0:
            cell = counts.setdefault(str(r.get("horizon_sessions")), {}).setdefault(due, [0, 0])
            cell[0] += 1 if ret > 0 else 0
            cell[1] += 1
    return counts


def prior_base_rates(rows, counts) -> dict:
    """Up-rate known before each forecast was made, per horizon.

    For a row with origin o, uses outcomes whose forecast target completed in
    the REFERENCE_WINDOW_SESSIONS before o: information available when the
    forecast was issued. 0.5 until enough history exists. Returns
    {id(row): (rate, basis)}.
    """
    from bisect import bisect_left

    prepared = {}
    for horizon, by_due in counts.items():
        dues = sorted(by_due)
        ups, totals = [0], [0]
        for d in dues:
            ups.append(ups[-1] + by_due[d][0])
            totals.append(totals[-1] + by_due[d][1])
        prepared[horizon] = (dues, ups, totals)
    starts, out = {}, {}
    for r in rows:
        origin = r.get("origin_session")
        dues, ups, totals = prepared.get(str(r.get("horizon_sessions")), ([], [0], [0]))
        if not origin or not dues:
            out[id(r)] = (0.5, "DEFAULT_INSUFFICIENT_HISTORY")
            continue
        if origin not in starts:
            starts[origin] = session_offset(origin, -REFERENCE_WINDOW_SESSIONS)
        lo, hi = bisect_left(dues, starts[origin]), bisect_left(dues, origin)
        n = totals[hi] - totals[lo]
        if n < REFERENCE_MINIMUM_OUTCOMES:
            out[id(r)] = (0.5, "DEFAULT_INSUFFICIENT_HISTORY")
        else:
            out[id(r)] = ((ups[hi] - ups[lo]) / n, "PRIOR_252_SESSIONS")
    return out


def pooled_statistics(rows, *, samples=None, seed=None, counts=None):
    """Pooled shadow evidence per horizon with date-block bootstrap ranges.

    Per-stock cells hold a handful of correlated outcomes each; pooling with
    block resampling gives one honest interval per horizon. Reports the
    candidate and current scores' information coefficients against the
    stock-specific move, their direction hit rates, and each forecast model's
    Brier skill against a prospective base rate: the up-rate of outcomes known
    before each forecast was issued (prior 252 sessions of reference_rows,
    default the rows themselves; 0.5 until 50 such outcomes). The in-sample
    base rate used before slightly favoured the reference. `counts` lets a
    caller supply precomputed history (see cumulative_summary).
    """
    import random

    from ..market_calendar import session_dates

    origins = sorted({r["origin_session"] for r in rows if r.get("origin_session")})
    epoch = min([STATS_EPOCH] + origins) if origins else STATS_EPOCH
    index = {}
    if origins:
        sessions = session_dates(epoch, origins[-1])
        position = {d: i for i, d in enumerate(sessions)}
        index = {o: position.get(o, 0) for o in origins}
    references = prior_base_rates(rows, reference_counts(rows) if counts is None else counts)
    by_horizon = defaultdict(list)
    for row in rows:
        by_horizon[row["horizon_sessions"]].append(row)
    result = {}
    for horizon, values in sorted(by_horizon.items()):
        blocks = defaultdict(lambda: defaultdict(float))
        models = set()
        for r in values:
            b = blocks[_block_of(r["origin_session"], horizon, index)]
            b["n"] += 1
            for suffix, z in (("", r.get("residual_z")), ("_sector", r.get("sector_relative_z"))):
                for name in ("candidate", "baseline"):
                    score = r.get(name + "_score")
                    if number(score) and number(z):
                        b[name + suffix + "_sz"] += score * z
                        b[name + suffix + "_ss"] += score * score
                        b[name + suffix + "_zz"] += z * z
            for name in ("candidate", "baseline"):
                hit = r.get(name + "_hit")
                if hit is not None:
                    b[name + "_hits"] += 1.0 if hit else 0.0
                    b[name + "_calls"] += 1
            ret = r.get("forecast_return_pct", r.get("realized_return_pct"))
            for model, metrics in (r.get("forecast_scores") or {}).items():
                brier = (metrics or {}).get("brier")
                if number(brier) and number(ret) and ret != 0:
                    models.add(model)
                    rate, basis = references[id(r)]
                    b["brier_sum:" + model] += brier
                    b["reference_sum:" + model] += (rate - (1.0 if ret > 0 else 0.0)) ** 2
                    b["count:" + model] += 1
                    if basis != "PRIOR_252_SESSIONS":
                        b["default_reference:" + model] += 1

        def measures(sums):
            out = {}
            for name in ("candidate", "baseline"):
                for suffix, label in (("", "_ic"), ("_sector", "_ic_vs_sector")):
                    ss, zz = sums.get(name + suffix + "_ss", 0.0), sums.get(name + suffix + "_zz", 0.0)
                    out[name + label] = (
                        sums.get(name + suffix + "_sz", 0.0) / sqrt(ss * zz) if ss > 0 and zz > 0 else None
                    )
                calls = sums.get(name + "_calls", 0)
                out[name + "_hit_rate"] = sums.get(name + "_hits", 0.0) / calls if calls else None
            out["ic_difference"] = (
                out["candidate_ic"] - out["baseline_ic"]
                if out["candidate_ic"] is not None and out["baseline_ic"] is not None
                else None
            )
            out["ic_difference_vs_sector"] = (
                out["candidate_ic_vs_sector"] - out["baseline_ic_vs_sector"]
                if out["candidate_ic_vs_sector"] is not None and out["baseline_ic_vs_sector"] is not None
                else None
            )
            for model in models:
                n = sums.get("count:" + model, 0)
                if not n:
                    out["brier_skill:" + model] = None
                    continue
                reference = sums.get("reference_sum:" + model, 0.0)
                out["brier_skill:" + model] = (
                    1 - sums["brier_sum:" + model] / reference if reference > 0 else None
                )
            return out

        def total(items):
            sums = defaultdict(float)
            for item in items:
                for k, v in item.items():
                    sums[k] += v
            return sums

        keys = sorted(blocks)
        point = measures(total(blocks[k] for k in keys))
        intervals = {}
        if len(keys) >= 2:
            draws_n = samples or BOOTSTRAP_SAMPLES
            rng = random.Random((BOOTSTRAP_SEED if seed is None else seed) + int(horizon))
            draws = defaultdict(list)
            for _ in range(draws_n):
                sample = measures(total(blocks[rng.choice(keys)] for _ in keys))
                for k, v in sample.items():
                    if v is not None:
                        draws[k].append(v)
            for k, values_k in draws.items():
                values_k.sort()
                if len(values_k) >= draws_n // 2:
                    intervals[k] = [
                        values_k[int(0.025 * len(values_k))],
                        values_k[int(0.975 * len(values_k)) - 1],
                    ]
        result[str(horizon)] = {
            "observations": len(values),
            "blocks": len(keys),
            "candidate_direction_calls": int(total(blocks.values()).get("candidate_calls", 0)),
            "baseline_direction_calls": int(total(blocks.values()).get("baseline_calls", 0)),
            "brier_reference": "prior 252-session base rate; 0.5 until 50 prior outcomes",
            "brier_default_reference_counts": {
                m: int(total(blocks.values()).get("default_reference:" + m, 0)) for m in sorted(models)
            },
            "point": point,
            "interval_95": intervals,
            "interval_basis": (
                "bootstrap over blocks of horizon-length origin sessions"
                if intervals else "fewer than two blocks; no interval"
            ),
        }
    return result


def _weights_detectable(interval, blocks, minimum_blocks):
    """80%-power detectable IC difference implied by the bootstrap interval."""
    if not interval:
        return {"detectable_difference_80pct": None, "detectable_at_minimum_blocks": None}
    se = (interval[1] - interval[0]) / (2 * 1.959964)
    mde = (1.959964 + 0.841621) * se
    return {
        "detectable_difference_80pct": mde,
        "detectable_at_minimum_blocks": mde * sqrt(blocks / max(blocks, minimum_blocks)),
    }


def weights_promotion_test(rows, protocol=WEIGHTS_PROTOCOL):
    """Pre-registered test: do frozen candidate weights beat the heuristic bias?

    Only outcomes whose snapshots carry this protocol's hash count, so
    changing the protocol (or the weighting it names) starts fresh evidence.
    """
    sha = digest(protocol)
    eligible = [r for r in rows if r.get("weights_protocol_sha256") == sha]
    stats = pooled_statistics(
        eligible, samples=protocol["bootstrap_samples"], seed=protocol["bootstrap_seed"]
    )
    primary = stats.get(str(protocol["primary_horizon_sessions"]))
    base = {
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": sha,
        "primary_horizon_sessions": protocol["primary_horizon_sessions"],
        "required_blocks": protocol["minimum_blocks"],
        "secondary": {h: v for h, v in stats.items() if h != str(protocol["primary_horizon_sessions"])},
        "promotion": "NOT_AUTHORIZED",
        "trade_qualification": False,
    }
    if not primary:
        return {**base, "status": "AWAITING_MATURED_OUTCOMES", "blocks": 0}
    point, interval = primary["point"], primary["interval_95"].get("ic_difference")
    summary = {
        **base,
        "blocks": primary["blocks"],
        "observations": primary["observations"],
        "candidate_ic": point.get("candidate_ic"),
        "incumbent_ic": point.get("baseline_ic"),
        "ic_difference": point.get("ic_difference"),
        "ic_difference_interval_95": interval,
        **_weights_detectable(interval, primary["blocks"], protocol["minimum_blocks"]),
    }
    if primary["blocks"] < protocol["minimum_blocks"] or interval is None:
        return {**summary, "status": "PENDING_EVIDENCE"}
    diff, candidate = point.get("ic_difference"), point.get("candidate_ic")
    passed = (
        diff is not None and diff > 0 and interval[0] > 0
        and candidate is not None and candidate > 0
    )
    return {**summary, "status": "PASS_REQUIRES_INDEPENDENT_REVIEW" if passed else "NOT_SUPERIOR"}


def cumulative_summary(conn) -> dict:
    """Everything the cumulative view needs, computed once (learning stage).

    The admin page reads this from the latest learning receipt instead of
    re-reading every outcome on each load.
    """
    rows = cumulative_rows(conn)
    counts = reference_counts(rows)
    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "outcomes": len(rows),
        "latest_due_session": max((r.get("due_session") or "" for r in rows), default=None) or None,
        "pooled": pooled_statistics(rows, counts=counts),
        "weights_promotion_test": weights_promotion_test(rows),
        "reference_counts": counts,
    }


def latest_precomputed(conn) -> dict | None:
    """The most recent cumulative summary saved by the learning stage."""
    row = conn.execute(
        """SELECT details->'shadow_cumulative' FROM operator_audit
        WHERE event='AGENT_STAGE_FINISHED' AND details->>'key' LIKE 'agent-stage:learning:%%'
          AND jsonb_typeof(details->'shadow_cumulative')='object'
          AND details->'shadow_cumulative' ? 'pooled'
        ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    return row[0] if row else None


def cumulative_rows(conn):
    """Every scored v2 outcome, reduced to the fields pooled statistics read."""
    rows = conn.execute(
        """SELECT jsonb_build_object(
                'horizon_sessions',payload->'horizon_sessions','origin_session',payload->'origin_session',
                'residual_z',payload->'residual_z','sector_relative_z',payload->'sector_relative_z',
                'candidate_score',payload->'candidate_score',
                'baseline_score',payload->'baseline_score','candidate_hit',payload->'candidate_hit',
                'baseline_hit',payload->'baseline_hit','realized_return_pct',payload->'realized_return_pct',
                'forecast_return_pct',payload->'forecast_return_pct',
                'due_session',payload->'due_session','forecast_due_session',payload->'forecast_due_session',
                'forecast_scores',payload->'forecast_scores',
                'weights_protocol_sha256',payload->'weights_protocol_sha256')
        FROM research_evidence_records WHERE kind=%s AND payload->>'target'=%s""",
        (OUTCOME, TARGET),
    ).fetchall()
    return [r[0] for r in rows]


def metric_mean(metrics, field):
    values = [m[field] for m in metrics if m[field] is not None]
    return sum(values) / len(values) if values else None


def weekly_summary(conn, *, end=None, cumulative=None):
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    end = end or latest_completed_session()
    start = session_offset(end, -5)
    rows = conn.execute(
        """SELECT payload FROM research_evidence_records
        WHERE kind=%s AND payload->>'due_session'>%s AND payload->>'due_session'<=%s
          AND payload->>'target'=%s
        ORDER BY recorded_at DESC,record_id DESC LIMIT 5001""",
        (OUTCOME, start, end, TARGET),
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
    if cumulative is None:
        cumulative = {**cumulative_summary(conn), "source": "LIVE_COMPUTATION"}
    else:
        cumulative = {**cumulative, "source": "LEARNING_STAGE_RECEIPT"}
    pooled = {
        "week": pooled_statistics(
            [row for (row,) in rows], counts=cumulative.get("reference_counts") or {}
        ),
        "cumulative": cumulative["pooled"],
    }
    return {
        "pooled": pooled,
        "cumulative_source": {k: cumulative.get(k) for k in (
            "source", "computed_at", "outcomes", "latest_due_session")},
        "weights_promotion_test": cumulative["weights_promotion_test"],
        "status": "ATTENTION" if overdue else ("OK" if rows else "AWAITING_MATURITY"),
        "period_start": start,
        "period_end": end,
        "matured": len(rows),
        "pending": pending,
        "due_pending": overdue,
        "rows": summary,
        "promotion": "NOT_AUTHORIZED",
        "broker_submission": False,
        "target": TARGET,
        "limitations": [
            "Directional comparison, not portfolio P&L or calibrated signal probabilities.",
            "Candidate and current scores are judged on the beta-adjusted move relative to SPY; agent actions on the raw move; forecast Brier and intervals on raw prices.",
            "Overlapping horizons and correlated stocks are not independent observations; pooled intervals resample date blocks, and per-stock rows carry no interval.",
            "Candidate weights are evaluated prospectively; no automatic promotion.",
        ],
    }
