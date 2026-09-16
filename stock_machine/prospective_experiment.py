"""A predeclared prospective experiment, never a qualified paper portfolio.

Freeze a 63-session momentum ranking of the registered universe; buy the top
quintile at a future session open and observe 20 sessions. No historical replay
or same-close fills. Simple controls, costs and kill criteria cannot be edited
after registration under the same experiment ID.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone

from . import db, research_store
from .financial_integrity import number
from .market_calendar import EASTERN, _calendar, latest_completed_session, session_offset
from .research_contract import digest, timestamp

EXPERIMENT_ID = "momentum63-hold20-prospective-v1"


def protocol(universe):
    return {"experiment_id": EXPERIMENT_ID, "version": 1, "universe": sorted(universe),
            "target": "20-session top-quintile long-only net total return minus both SPY and frozen-universe equal-weight controls",
            "signal": "63-session adjusted-close momentum; descending, ticker breaks ties",
            "lookback_sessions": 63, "holding_sessions": 20, "tail_fraction": .2,
            "entry": "next exchange open strictly after forecast persistence", "exit": "20th holding-session close",
            "cost_bps_each_side": 25, "stress_cost_bps_each_side": 50,
            "minimum_nonoverlapping_cohorts": 12, "minimum_excess_pct": 0,
            "minimum_mean_stress_net_return_pct": 0,
            "max_cohort_drawdown_pct": -20, "confidence": "paired cohort bootstrap lower 95% bound must exceed zero against both controls",
            "bootstrap_seed": 20260916, "bootstrap_samples": 5000,
            "failure_policy": "no silent ticker/date exclusions; missing outcomes block evaluation; any cohort drawdown below -20% rejects; failed final criteria reject",
            "controls": ["SPY", "equal_weight_frozen_universe"],
            "qualification": "EXPERIMENT_ONLY_NO_TRADE_OR_PAPER_PROMOTION",
            "limitations": ["Universe selected from current coverage, not an unbiased historical universe",
                            "Costs are explicit assumptions, not measured execution",
                            "No shorting, leverage or options; no borrowing assumption",
                            "A passing result requires independent review and does not authorize orders"]}


def next_entry(now):
    now = now.astimezone(EASTERN)
    cal = _calendar(now.year, now.year + 1)
    session = cal.date_to_session(now.date().isoformat(), direction="next")
    # Allow processing time before the open. Use a future calendar date even
    # for an early-morning worker; delayed persistence must still precede open.
    if session.date() <= now.date():
        session = cal.next_session(session)
    return session.date().isoformat()


def freeze(config, histories, *, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = latest_completed_session(now)
    lookback = session_offset(cutoff, -config["lookback_sessions"])
    scores, inputs, missing = {}, {}, []
    for ticker in config["universe"]:
        rows = {r["date"]: r for r in histories.get(ticker, []) if r["date"] <= cutoff}
        first, last = rows.get(lookback, {}), rows.get(cutoff, {})
        a, b = number(first.get("adj_close")), number(last.get("adj_close"))
        if a is None or b is None or min(a, b) <= 0:
            missing.append(ticker)
            continue
        scores[ticker] = b / a - 1
        used = [rows[d] for d in sorted(rows) if lookback <= d <= cutoff]
        inputs[ticker] = {"price_window_sha256": digest(used), "from": lookback, "through": cutoff,
                          "start_adjusted_close": a, "end_adjusted_close": b}
    if missing or not scores:
        return {"status": "BLOCKED_INPUTS", "missing": missing, "as_of": cutoff, "experiment_id": EXPERIMENT_ID}
    ranking = sorted(scores, key=lambda t: (-scores[t], t))
    selected = ranking[:max(1, math.ceil(len(ranking) * config["tail_fraction"]))]
    entry = next_entry(now)
    return {"status": "FROZEN", "experiment_id": EXPERIMENT_ID, "as_of": cutoff,
            "created_at": now.isoformat(), "entry_date": entry,
            "exit_date": session_offset(entry, config["holding_sessions"] - 1),
            "selected": selected, "universe": config["universe"], "ranking": ranking,
            "scores": scores, "input_identities": inputs, "protocol_sha256": digest(config),
            "trade_execution": False, "fill_status": "FUTURE_OBSERVATION_NOT_A_FILL"}


def score(config, forecast, histories, *, now=None, recorded_at=None):
    now = now or datetime.now(timezone.utc)
    entry, end = forecast["entry_date"], forecast["exit_date"]
    if latest_completed_session(now) < end:
        return {"status": "PENDING_MATURITY", "due_session": end}
    cal = _calendar(int(entry[:4]), int(end[:4]))
    created = timestamp(forecast.get("created_at"))
    recorded = timestamp(recorded_at)
    if not created or not recorded or max(created, recorded) >= cal.session_open(entry).to_pydatetime():
        return {"status": "INVALID_PROSPECTIVE_TIMING"}
    sessions = [s.date().isoformat() for s in cal.sessions_in_range(entry, end)]
    cumulative, endpoints, missing = {}, {}, []
    for ticker in sorted(set(forecast["universe"] + ["SPY"])):
        rows = {r["date"]: r for r in histories.get(ticker, [])}
        first = rows.get(entry, {})
        opening, close, adjusted = (number(first.get(k)) for k in ("open", "close", "adj_close"))
        values = [number(rows.get(d, {}).get("adj_close")) for d in sessions]
        if (any(v is None or v <= 0 for v in (opening, close, adjusted))
                or any(v is None or v <= 0 for v in values)):
            missing.append(ticker)
            continue
        # Both endpoints use one observed adjustment vintage; store that
        # evidence with the outcome so future provider revisions cannot rewrite it.
        adjusted_open = opening * adjusted / close
        cumulative[ticker] = [v / adjusted_open - 1 for v in values]
        endpoints[ticker] = {"entry": first, "exit": rows[end], "path_sha256": digest([rows[d] for d in sessions])}
    if missing:
        return {"status": "BLOCKED_MISSING_OUTCOMES", "missing": missing, "due_session": end}
    cost = 2 * config["cost_bps_each_side"] / 10000
    stress = 2 * config["stress_cost_bps_each_side"] / 10000
    def basket(names):
        path = [sum(cumulative[t][i] for t in names) / len(names) for i in range(len(sessions))]
        path = [r - config["cost_bps_each_side"] / 10000 for r in path]
        path[-1] -= config["cost_bps_each_side"] / 10000
        peak, drawdown = 1., 0.
        for r in path:
            nav = 1 + r
            peak = max(peak, nav)
            drawdown = min(drawdown, nav / peak - 1)
        return {"net_return_pct": path[-1] * 100, "stress_net_return_pct": (path[-1] + cost - stress) * 100,
                "max_drawdown_pct": drawdown * 100}
    challenger, spy, equal = basket(forecast["selected"]), basket(["SPY"]), basket(forecast["universe"])
    return {"status": "SCORED", "experiment_id": EXPERIMENT_ID, "forecast_as_of": forecast["as_of"],
            "entry_date": entry, "exit_date": end, "scored_at": now.isoformat(),
            "challenger": challenger, "SPY": spy, "equal_weight": equal,
            "excess_vs_spy_pct": challenger["net_return_pct"] - spy["net_return_pct"],
            "excess_vs_equal_weight_pct": challenger["net_return_pct"] - equal["net_return_pct"],
            "price_evidence": endpoints, "forecast_sha256": digest(forecast), "protocol_sha256": digest(config)}


def verdict(config, outcomes):
    rows = [r for r in outcomes if r.get("status") == "SCORED"]
    if any(r["challenger"]["max_drawdown_pct"] < config["max_cohort_drawdown_pct"] for r in rows):
        return {"status": "REJECTED", "reason": "DRAWDOWN_KILL_CRITERION", "cohorts": len(rows)}
    if len(rows) < config["minimum_nonoverlapping_cohorts"]:
        return {"status": "PENDING_MATURITY", "cohorts": len(rows), "required": config["minimum_nonoverlapping_cohorts"]}
    ordered = sorted(rows, key=lambda r: r["entry_date"])
    if any(a["exit_date"] >= b["entry_date"] for a, b in zip(ordered, ordered[1:])):
        return {"status": "INVALID_OVERLAPPING_COHORTS"}
    stats = {}
    for key in ("excess_vs_spy_pct", "excess_vs_equal_weight_pct"):
        values = [r[key] for r in rows]
        rng = random.Random(config["bootstrap_seed"])
        draws = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(config["bootstrap_samples"]))
        stats[key] = {"mean": sum(values) / len(values), "lower_95pct": draws[int(.025 * len(draws))]}
    stress_mean = sum(r["challenger"]["stress_net_return_pct"] for r in rows) / len(rows)
    passed = all(s["mean"] > 0 and s["lower_95pct"] > 0 for s in stats.values()) and stress_mean > config["minimum_mean_stress_net_return_pct"]
    return {"status": "PASS_REQUIRES_INDEPENDENT_REVIEW" if passed else "REJECTED", "cohorts": len(rows),
            "paired_results": stats, "mean_stress_net_return_pct": stress_mean, "trade_qualification": False}


def run():
    with db.connect() as lock:
        lock.autocommit = True
        if not lock.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (EXPERIMENT_ID,)).fetchone()[0]:
            return {"status": "BUSY", "experiment_id": EXPERIMENT_ID}
        try:
            with db.connect() as conn:
                registered = research_store.get(conn, "EXPERIMENT_PROTOCOL", EXPERIMENT_ID)
                if not registered:
                    config = protocol([r["ticker"] for r in db.list_companies(conn)])
                    registered = research_store.save(conn, "EXPERIMENT_PROTOCOL", EXPERIMENT_ID, config)
                config = registered["payload"]
                if protocol(config["universe"]) != config:
                    raise ValueError("Experiment protocol changed; a new reviewed ID is required")
                histories = {t: db.fetch_prices(conn, t) for t in sorted(set(config["universe"] + ["SPY"]))}
                all_forecasts = research_store.records(conn, "EXPERIMENT_FORECAST")
                outcomes = research_store.records(conn, "EXPERIMENT_OUTCOME")
            forecasts = [r for r in all_forecasts if r["payload"].get("experiment_id") == EXPERIMENT_ID]
            outcome_ids = {r["payload"].get("forecast_sha256") for r in outcomes}
            for row in forecasts:
                frozen = row["payload"]
                if digest(frozen) in outcome_ids:
                    continue
                result = score(config, frozen, histories, recorded_at=row["recorded_at"])
                if result["status"] == "SCORED":
                    with db.connect() as conn:
                        outcomes.append(research_store.save(conn, "EXPERIMENT_OUTCOME", row["record_id"], result))
                elif result["status"] != "PENDING_MATURITY":
                    return {"experiment_id": EXPERIMENT_ID, **result, "new_forecast": False}
            evaluation = verdict(config, [r["payload"] for r in outcomes if r["payload"].get("experiment_id") == EXPERIMENT_ID])
            if evaluation["status"] in ("REJECTED", "PASS_REQUIRES_INDEPENDENT_REVIEW"):
                return {"experiment_id": EXPERIMENT_ID, "evaluation": evaluation, "new_forecast": False}
            if forecasts and forecasts[-1]["payload"]["exit_date"] >= latest_completed_session():
                return {"experiment_id": EXPERIMENT_ID, "evaluation": evaluation, "new_forecast": False,
                        "active_forecast": forecasts[-1]["payload"]}
            frozen = freeze(config, histories)
            if frozen["status"] != "FROZEN":
                return frozen
            with db.connect() as conn:
                record = research_store.save(conn, "EXPERIMENT_FORECAST", EXPERIMENT_ID + ":" + frozen["as_of"], frozen)
            return {"experiment_id": EXPERIMENT_ID, "evaluation": evaluation, "new_forecast": True,
                    "forecast_record": record["record_id"], "active_forecast": frozen}
        finally:
            lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (EXPERIMENT_ID,))
