"""Frozen ledger analytics. No ticker downloads, no hidden zero/forward filling."""
from __future__ import annotations
from datetime import date
from decimal import Decimal
from html import escape
from math import isfinite, sqrt
from statistics import mean, stdev
from .contracts import digest

INPUT_KIND = "ANALYTICS_INPUT_V1"
QUANTSTATS_VERSION = "0.0.81"
MIN_RISK_OBSERVATIONS = 30
MAX_SESSIONS = 504


def _number(value):
    if isinstance(value, bool):
        raise ValueError("INVALID_VALUATION_NUMBER")
    try:
        number = Decimal(str(value))
    except Exception:
        raise ValueError("INVALID_VALUATION_NUMBER") from None
    if not number.is_finite():
        raise ValueError("INVALID_VALUATION_NUMBER")
    return number


def performance_series(valuations: list[dict], expected_sessions: list[str]) -> dict:
    """Inception series only. Subsequent external flows need subperiod valuations."""
    if not valuations:
        return {"status": "NO_HISTORY", "returns": [], "points": [], "blockers": ["NO_SAVED_VALUATIONS"]}
    dates = [v.get("session") for v in valuations]
    if dates != expected_sessions or dates != sorted(set(dates)):
        return {"status": "WITHHELD", "returns": [], "points": [], "blockers": ["SESSION_COVERAGE_MISMATCH"]}
    base = _number(valuations[0]["external_flows_usd"])
    if base <= 0:
        return {"status": "WITHHELD", "returns": [], "points": [], "blockers": ["INITIAL_CAPITAL_UNAVAILABLE"]}
    prior, high, returns, points, blockers = base, base, [], [], []
    for row in valuations:
        day = row["session"]
        if row.get("status") != "COMPLETE" or row.get("equity_usd") is None:
            blockers.append("MISSING_MARK:" + day)
            points.append({"time": day})
            continue
        equity = _number(row["equity_usd"])
        if _number(row["external_flows_usd"]) != base:
            blockers.append("SUBPERIOD_CASHFLOW_VALUATIONS_REQUIRED:" + day)
        if equity <= 0 or prior <= 0:
            blockers.append("NONPOSITIVE_EQUITY:" + day)
        points.append({"time": day, "value": float(equity)})
        if equity > 0 and prior > 0:
            returns.append(float(equity/prior-1))
            high = max(high, equity)
            points[-1]["drawdown"] = float(equity/high-1)
            prior = equity
    if blockers:
        return {"status": "WITHHELD", "returns": [], "points": points, "blockers": blockers,
                "initial_capital_usd": str(base)}
    compounded = prior/base-1
    sd = stdev(returns) if len(returns) >= 2 else 0.0
    sufficient = len(returns) >= MIN_RISK_OBSERVATIONS
    return {"status": "OK", "returns": returns, "points": points, "blockers": [],
            "initial_capital_usd": str(base), "periods": len(returns), "return_units": "DECIMAL",
            "metrics": {"total_return": float(compounded), "max_drawdown": min(0., *(p["drawdown"] for p in points)),
                        "positive_period_fraction": sum(r > 0 for r in returns)/len(returns),
                        "annualized_volatility": sd*sqrt(252) if sufficient else None,
                        "sharpe_rf_zero": mean(returns)/sd*sqrt(252) if sufficient and sd > 1e-12 else None},
            "risk_metrics_status": "AVAILABLE" if sufficient else "INSUFFICIENT_HISTORY",
            "conventions": {"annualization": 252, "risk_free_rate": 0,
                            "minimum_risk_observations": MIN_RISK_OBSERVATIONS,
                            "positive_periods_are_not_completed_trade_wins": True,
                            "inception_capital_is_before_first_recorded_fill": True}}


def _quantstats(series: dict) -> dict:
    """Optional worker-only dependency. Reconcile totals before exposing results."""
    import importlib.metadata
    import pandas as pd
    import quantstats as qs
    installed = importlib.metadata.version("quantstats")
    if installed != QUANTSTATS_VERSION:
        raise ValueError("QUANTSTATS_VERSION_NOT_REVIEWED")
    values = pd.Series(series["returns"], index=pd.to_datetime([p["time"] for p in series["points"]]), dtype=float)
    total = float(qs.stats.comp(values))
    if not isfinite(total) or abs(total-series["metrics"]["total_return"]) > 1e-8:
        raise ValueError("QUANTSTATS_TOTAL_RECONCILIATION_FAILED")
    result = {"version": installed, "status": "OK", "total_return": total,
              "annualized_volatility": None, "sharpe_rf_zero": None}
    # Some QS helpers infer prices when all returns are positive and max > 1.
    # Never feed ambiguous series through that automatic preparation path.
    if series["periods"] >= MIN_RISK_OBSERVATIONS and all(-1 < r <= 1 for r in series["returns"]):
        vol = float(qs.stats.volatility(values, periods=252, annualize=True, prepare_returns=False))
        expected = series["metrics"]["annualized_volatility"]
        if not isfinite(vol) or abs(vol-expected) > 1e-8:
            raise ValueError("QUANTSTATS_VOLATILITY_RECONCILIATION_FAILED")
        result["annualized_volatility"] = vol
        if expected > 1e-12:
            ratio = float(qs.stats.sharpe(values, rf=0, periods=252, annualize=True))
            if not isfinite(ratio) or abs(ratio-series["metrics"]["sharpe_rf_zero"]) > 1e-8:
                raise ValueError("QUANTSTATS_SHARPE_RECONCILIATION_FAILED")
            result["sharpe_rf_zero"] = ratio
    else:
        result["risk_metrics_reason"] = "SHORT_HISTORY_OR_AMBIGUOUS_RETURN_RANGE"
    return result


def render_report(snapshot: dict, *, with_quantstats=True) -> dict:
    if snapshot.get("schema_version") != "performance-input.v1":
        raise ValueError("REPORT_INPUT_VERSION_INVALID")
    series = performance_series(snapshot.get("valuations") or [], snapshot.get("sessions") or [])
    quantstats = {"status": "NOT_REQUESTED"}
    if with_quantstats and series["status"] == "OK":
        try:
            quantstats = _quantstats(series)
        except ImportError:
            quantstats = {"status": "DEPENDENCY_UNAVAILABLE"}
    benchmark = {"status": "WITHHELD", "reason": "BENCHMARK_COVERAGE_MISMATCH", "points": []}
    data = snapshot.get("benchmark") or []
    days = snapshot.get("sessions") or []
    if days and [r["date"] for r in data] == days:
        prices = [_number(r.get("adj_close")) for r in data]
        if all(p > 0 for p in prices):
            initial = _number(series.get("initial_capital_usd", 100000))
            benchmark = {"status": "OK", "symbol": "SPY", "basis": "STORED_ADJUSTED_CLOSE",
                         "anchor": "FIRST_REPORTED_SESSION_CLOSE", "points": [
                             {"time": d, "value": float(initial*p/prices[0])} for d, p in zip(days, prices)]}
    result = {"schema_version": "performance-report.v1", "portfolio_id": snapshot["portfolio_id"],
              "quality": snapshot["quality"], "status": series["status"], "input_sha256": digest(snapshot),
              "series": series, "quantstats": quantstats, "benchmark": benchmark,
              "limitations": snapshot.get("limitations") or [], "broker_submission": False}
    metrics = series.get("metrics") or {}
    rows = "".join(f"<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>" for k, v in metrics.items())
    result["html"] = ("<!doctype html><meta charset=utf-8><title>Portfolio report</title>"
                      f"<h1>{escape(snapshot['portfolio_id'])}</h1><p>{escape(snapshot['quality'])}</p>"
                      f"<p>Status: {escape(series['status'])} · QuantStats: {escape(str(quantstats.get('status')))}</p>"
                      f"<table>{rows}</table><p>Return units are decimal; positive periods are not trade wins.</p>"
                      + "".join(f"<p>{escape(str(x))}</p>" for x in result["limitations"]))
    return result


def freeze_input(conn, portfolio_id: str) -> dict:
    """Called inside a fresh REPEATABLE READ transaction. No provider requests."""
    from .. import db, research_store
    from ..market_calendar import latest_completed_session, _calendar
    from .legacy import PORTFOLIO, export_v1, timeline
    from .ledger import load_events
    if portfolio_id == PORTFOLIO:
        exported = export_v1(conn)
        events = exported["events"]
        limitations = exported.get("limitations") or [exported["status"]]
    else:
        events = load_events(conn, portfolio_id)
        limitations = []
    sessions, valuations, benchmark = [], [], []
    if events:
        first, last = events[0].occurred_at.date().isoformat(), latest_completed_session()
        a, b = date.fromisoformat(first), date.fromisoformat(last)
        if first > last:
            raise ValueError("NO_COMPLETED_VALUATION_SESSION")
        sessions = [s.date().isoformat() for s in _calendar(a.year, b.year).sessions_in_range(first, last)]
        if len(sessions) > MAX_SESSIONS:
            raise ValueError("PERFORMANCE_CHECKPOINT_REQUIRED")
        valuations = timeline(events, sessions)
        prices = {r["date"]: r for r in db.fetch_prices(conn, "SPY", last)}
        benchmark = [{"date": d, "adj_close": str(prices[d]["adj_close"])}
                     for d in sessions if d in prices and prices[d].get("adj_close") is not None]
    snapshot = {"schema_version": "performance-input.v1", "portfolio_id": portfolio_id,
                "quality": events[0].quality if events else "NO_HISTORY", "sessions": sessions,
                "valuations": valuations, "benchmark": benchmark,
                "events_sha256": digest([e.model_dump(mode="json") for e in events]), "limitations": limitations}
    key = digest(snapshot)
    research_store.save(conn, INPUT_KIND, key, snapshot)
    return {"input_key": key, "portfolio_id": portfolio_id}


def build_report_job(payload: dict) -> dict:
    from .. import db, research_store
    if set(payload) != {"input_key", "portfolio_id"}:
        raise ValueError("PERFORMANCE_JOB_PAYLOAD_INVALID")
    with db.connect() as conn:
        record = research_store.get(conn, INPUT_KIND, payload["input_key"])
    if not record or digest(record["payload"]) != payload["input_key"]:
        raise ValueError("PERFORMANCE_SNAPSHOT_HASH_MISMATCH")
    snapshot = record["payload"]
    if snapshot["portfolio_id"] != payload["portfolio_id"]:
        raise ValueError("PERFORMANCE_PORTFOLIO_MISMATCH")
    return render_report(snapshot)
