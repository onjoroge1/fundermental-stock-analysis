"""Forward tracking of the sector P/E fair-value rule: a confirmatory test.

The replay (fair-value-convergence.v1, docs/evidence/fair-value-replay-2026-10-18.md)
found one positive but not significant policy, "sector_timed_120". This module
tests that single policy on data the replay never saw:

- Freeze: at each month-end session S, before the open of the next session,
  the decision for every stock in the frozen universe is computed with the
  replay's own code (fair_value_replay.observe_session / sector_gaps /
  decision_row) and stored once as an append-only record.
- Score: once the 120-session window after S+1 has completed, each stock is
  scored with reward v5 from one price vintage; FLAT scores zero. A stock
  whose window still lacks bars RETRY_SESSIONS sessions after it is due is
  excluded, visibly.
- Evaluate: monthly means, Newey-West lag 6, t(n-1) 97.5%, once at least 24
  months had an active position. A pass only authorizes review.

Nothing here trades, sizes positions or changes paper or live settings.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from statistics import mean

from ..agents.contracts import AGENT_UNIVERSE, digest
from ..backtest import direction_replay as dr
from ..backtest import fair_value_replay as fv
from ..market_calendar import _calendar, latest_completed_session, session_dates, session_offset, session_on_or_before
from . import inference

FORWARD_ID = "fair-value-convergence.v1:sector_timed_120:forward.v1"
POLICY = "sector_timed_120"
HORIZON = 120
FIRST_DECISION_MONTH = "2026-10"
RETRY_SESSIONS = 10
MIN_ACTIVE_MONTHS = 24
FINAL_ATTEMPT_HOURS = 4
# The replay protocol this tracker confirms; any edit to it is a new tracker.
REPLAY_PROTOCOL_SHA256 = "4b151cdfc7777f8adbc73eafbd51a6f98ead5c601b6788b4faf47b727ce0bda3"

PROTOCOL_KIND = "FV_FORWARD_PROTOCOL_V1"
DECISION_KIND = "FV_FORWARD_DECISION_V1"
OUTCOME_KIND = "FV_FORWARD_OUTCOME_V1"
CHECK_KIND = "FV_FORWARD_CHECK_V1"
EVALUATION_KIND = "FV_FORWARD_EVALUATION_V1"
ACTOR = "fair-value-scheduler"

DECISION_SOURCES = (
    "backtest/fair_value_replay.py", "backtest/direction_replay.py", "backtest/engine.py",
    "agent_intelligence/features.py", "agent_intelligence/state.py", "agent_intelligence/reward.py",
    "features/metrics.py", "features/scoring.py", "regime.py",
)
FEATURES_KEPT = ("beta_63_vs_spy", "realized_vol_20", "realized_vol_60", "momentum_63",
                 "relative_momentum_63_vs_spy", "price")

PROTOCOL = {
    "forward_id": FORWARD_ID,
    "confirms": {"protocol_id": fv.PROTOCOL["protocol_id"], "protocol_sha256": REPLAY_PROTOCOL_SHA256,
                 "policy": POLICY, "evidence": "docs/evidence/fair-value-replay-2026-10-18.md"},
    "policy": {"fair_value": fv.PROTOCOL["fair_value"]["sector"], "solid_company": fv.PROTOCOL["solid_company"],
               "trade": fv.PROTOCOL["trade"], "window_sessions": HORIZON},
    "universe": sorted(AGENT_UNIVERSE),
    "decision_session": "last XNYS session of each calendar month, from " + FIRST_DECISION_MONTH,
    "freeze_deadline": "before the open of the session after the decision session; a later freeze is "
                       "recorded as FREEZE_MISSED and that month is lost, never reconstructed",
    "data_rule": "freeze when every universe stock and SPY has a bar at the decision session; on the final "
                 f"attempt (within {FINAL_ATTEMPT_HOURS}h of the deadline) freeze anyway and record the stocks "
                 "without a bar as NO_DECISION_MISSING_BAR",
    "window": f"entry at the close of the session after the decision session; exit {HORIZON} sessions later",
    "reward": "risk-scaled-residual-paper-reward.v5 from one adjusted-price vintage read at scoring; FLAT = 0",
    "missing_bars": f"retry daily; {RETRY_SESSIONS} sessions after the due session a stock still without a "
                    "complete window is EXCLUDED_MISSING_BARS and omitted from that month's mean",
    "evaluation": "monthly mean of scored stocks (FLAT = 0); Newey-West lag ceil(120/21) = 6; Student "
                  f"t(n-1) 97.5%; at least {MIN_ACTIVE_MONTHS} months with an active position; SUPPORTED if "
                  "the mean and its lower 95% bound are both positive",
    "single_hypothesis": "one pre-specified policy is confirmed, so no multiple-testing correction applies",
    "timing_validity": "a decision whose creation or database recording is at or after the entry session's "
                       "open is INVALID_PROSPECTIVE_TIMING and never scored",
    "qualification": "PASS_REQUIRES_INDEPENDENT_REVIEW; promotion NOT_AUTHORIZED; broker_submission false",
}
PROTOCOL_SHA256 = digest(PROTOCOL)


# ---- calendar ------------------------------------------------------------------

def month_end_session(day: str) -> str:
    d = date.fromisoformat(day)
    return session_on_or_before(date(d.year, d.month, monthrange(d.year, d.month)[1]).isoformat())


def is_decision_session(session: str) -> bool:
    return session[:7] >= FIRST_DECISION_MONTH and month_end_session(session) == session


def session_open(session: str) -> datetime:
    year = int(session[:4])
    return _calendar(year, year).session_open(session).to_pydatetime()


def expected_decision_session(completed: str) -> str | None:
    """Most recent decision session on or before the latest completed session."""
    candidate = month_end_session(completed)
    if candidate > completed:
        first = date.fromisoformat(completed[:7] + "-01") - timedelta(days=1)
        candidate = month_end_session(first.isoformat())
    return candidate if candidate[:7] >= FIRST_DECISION_MONTH else None


def windows(session: str) -> tuple[str, str]:
    entry = session_offset(session, 1)
    return entry, session_offset(entry, HORIZON)


# ---- payload hygiene -------------------------------------------------------------

def clean(value):
    """JSON-safe copy: non-finite floats become None (digest() rejects NaN)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def source_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    return hashlib.sha256(b"".join((root / p).read_bytes() for p in DECISION_SOURCES)).hexdigest()


# ---- freeze ----------------------------------------------------------------------

def decide_month(universe: dict, session: str, *, spy: list[dict], regime_for=None) -> list[dict]:
    """Every universe stock's frozen decision at one session (shared replay code)."""
    observed = fv.observe_session(universe, session)
    gaps = fv.sector_gaps({t: (sector, pe) for t, (_, _, sector, pe) in observed.items()})
    rows = []
    for ticker in sorted(universe):
        observation, detail, sector, pe = observed[ticker]
        made = fv.decision_row(ticker, session, universe[ticker], observation=observation, detail=detail,
                               own_gap=None, sector_gap=gaps.get(ticker), spy=spy,
                               regime=regime_for(ticker, session) if regime_for else None)
        base = {"ticker": ticker, "sector": sector, "pe_ttm": pe,
                "profitability_score": detail.get("profitability_score"),
                "financial_health_score": detail.get("financial_health_score")}
        if made is None:
            rows.append({**base, "side": "NO_DECISION", "reason": "NO_BAR_OR_INSUFFICIENT_HISTORY"})
            continue
        row, decision = made
        rows.append({**base, "side": row[POLICY], "solid": row["solid"], "sector_gap": row["sector_gap"],
                     "technical_signal": row["technical_signal"],
                     "features": {k: decision["features"].get(k) for k in FEATURES_KEPT}})
    return clean(rows)


def decision_payload(session: str, rows: list[dict], *, protocol: dict, created_at: datetime,
                     missing: list[str]) -> dict:
    entry, due = windows(session)
    for r in rows:
        if r["ticker"] in missing:
            r.update(side="NO_DECISION", reason="NO_DECISION_MISSING_BAR")
    counts = {s: sum(r["side"] == s for r in rows) for s in ("LONG", "SHORT", "FLAT", "NO_DECISION")}
    return {
        "forward_id": FORWARD_ID, "protocol_record_id": protocol["record_id"],
        "protocol_content_hash": protocol["content_hash"], "decision_session": session,
        "entry_session": entry, "due_session": due, "created_at": created_at.isoformat(),
        "code": {"git_sha": os.getenv("GITHUB_SHA"), "decision_sources_sha256": source_fingerprint()},
        "missing_bars_at_freeze": sorted(missing), "counts": counts, "rows": rows,
        "promotion": "NOT_AUTHORIZED", "broker_submission": False,
    }


# ---- scoring ---------------------------------------------------------------------

def _parse(stamp: str) -> datetime:
    text = str(stamp).replace("Z", "+00:00").replace(" ", "T")
    if re.search(r"[+-]\d\d$", text):  # Postgres prints "+00"; older Pythons need "+00:00"
        text += ":00"
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def timing_valid(decision: dict) -> bool:
    payload = decision["payload"]
    deadline = session_open(payload["entry_session"])
    return _parse(payload["created_at"]) < deadline and _parse(decision["recorded_at"]) < deadline


def score_month(decision_payload_: dict, *, prices: dict, spy: list[dict], completed: str) -> dict:
    """Outcome for one frozen month, or {"status": "PENDING", "blocked": [...]}."""
    session, due = decision_payload_["decision_session"], decision_payload_["due_session"]
    spy_adj = dr._adjusted(spy)
    expired = completed >= session_offset(due, RETRY_SESSIONS)
    results, blocked = [], []
    for r in decision_payload_["rows"]:
        if r["side"] == "NO_DECISION":
            results.append({"ticker": r["ticker"], "status": "NO_DECISION", "side": "NO_DECISION"})
            continue
        stock_adj = dr._adjusted(prices.get(r["ticker"]) or [])
        outcome = dr.rewards(session, {"features": r["features"]}, stock=stock_adj, spy=spy_adj, horizon=HORIZON)
        if outcome is None:
            if expired:
                results.append({"ticker": r["ticker"], "status": "EXCLUDED_MISSING_BARS", "side": r["side"]})
            else:
                blocked.append(r["ticker"])
            continue
        path = [stock_adj.get(d) for d in session_dates(outcome["entry_session"], outcome["due_session"])]
        score = 0.0 if r["side"] == "FLAT" else float(outcome["rewards"][r["side"]])
        results.append({"ticker": r["ticker"], "status": "SCORED", "side": r["side"], "score": score,
                        "rewards": outcome["rewards"], "beta": outcome["beta"], "path_sha256": digest(path)})
    if blocked:
        return {"status": "PENDING", "blocked": sorted(blocked)}
    scored = [x for x in results if x["status"] == "SCORED"]
    spy_path = [spy_adj.get(d) for d in session_dates(*windows(session))]
    return clean({
        "status": "SCORED", "forward_id": FORWARD_ID, "decision_session": session,
        "due_session": due, "scored_through": completed, "results": results,
        "monthly_mean": mean(x["score"] for x in scored) if scored else None,
        "active": any(x["side"] in ("LONG", "SHORT") for x in scored),
        "scored": len(scored), "excluded": sum(x["status"] == "EXCLUDED_MISSING_BARS" for x in results),
        "spy_path_sha256": digest(spy_path),
    })


# ---- evaluation ------------------------------------------------------------------

def evaluate(outcomes: list[dict]) -> dict:
    """outcomes: SCORED outcome payloads, any order."""
    months = sorted((o for o in outcomes if o.get("status") == "SCORED" and o.get("monthly_mean") is not None),
                    key=lambda o: o["decision_session"])
    values = [o["monthly_mean"] for o in months]
    active = sum(bool(o["active"]) for o in months)
    out = {"months": len(values), "active_months": active, "min_active_months": MIN_ACTIVE_MONTHS,
           "mean": mean(values) if values else None}
    if active < MIN_ACTIVE_MONTHS or len(values) < 3:
        return {**out, "status": "PENDING_EVIDENCE"}
    se = inference.newey_west_se(values, lag=ceil(HORIZON / 21))
    t = inference.t_quantile_975(len(values) - 1)
    lower, upper = out["mean"] - t * se, out["mean"] + t * se
    passed = out["mean"] > 0 and lower > 0
    return {**out, "newey_west_se": se, "lower_95": lower, "upper_95": upper,
            "status": "PASS_REQUIRES_INDEPENDENT_REVIEW" if passed else "NOT_SUPPORTED",
            "promotion": "NOT_AUTHORIZED"}


# ---- database orchestration ------------------------------------------------------

def _rows(conn, sql, params):
    return [dict(zip(("request_key", "record_id", "content_hash", "payload", "recorded_at"), r))
            for r in conn.execute(sql, params).fetchall()]


def unscored_due(conn, completed: str) -> list[dict]:
    return _rows(conn, """SELECT d.request_key,d.record_id,d.content_hash,d.payload,d.recorded_at::text
        FROM research_evidence_records d
        WHERE d.kind=%s AND d.payload->>'forward_id'=%s AND d.payload->>'due_session'<=%s
          AND NOT EXISTS (SELECT 1 FROM research_evidence_records o
                          WHERE o.kind=%s AND o.request_key=d.request_key)
        ORDER BY d.payload->>'decision_session'""", (DECISION_KIND, FORWARD_ID, completed, OUTCOME_KIND))


def outcomes(conn) -> list[dict]:
    return [r["payload"] for r in _rows(conn, """SELECT request_key,record_id,content_hash,payload,recorded_at::text
        FROM research_evidence_records WHERE kind=%s AND payload->>'forward_id'=%s
        ORDER BY payload->>'decision_session'""", (OUTCOME_KIND, FORWARD_ID))]


def decisions(conn) -> list[dict]:
    return _rows(conn, """SELECT request_key,record_id,content_hash,payload,recorded_at::text
        FROM research_evidence_records WHERE kind=%s AND payload->>'forward_id'=%s
        ORDER BY payload->>'decision_session'""", (DECISION_KIND, FORWARD_ID))


def checks(conn, limit: int = 20) -> list[dict]:
    return _rows(conn, """SELECT request_key,record_id,content_hash,payload,recorded_at::text
        FROM research_evidence_records WHERE kind=%s AND payload->>'forward_id'=%s
        ORDER BY recorded_at DESC LIMIT %s""", (CHECK_KIND, FORWARD_ID, limit))


def bars_missing(conn, session: str, tickers) -> list[str]:
    have = {r[0] for r in conn.execute(
        "SELECT ticker FROM prices_daily WHERE date=%s AND ticker=ANY(%s) AND adj_close > 0",
        (session, list(tickers))).fetchall()}
    return sorted(set(tickers) - have)


def _save_check(conn, key: str, payload: dict) -> None:
    from .. import research_store
    research_store.save(conn, CHECK_KIND, key, clean({"forward_id": FORWARD_ID, **payload}))


def score_pending(conn, completed: str) -> dict:
    from .. import db, research_store
    scored, pending, invalid = [], [], []
    for d in unscored_due(conn, completed):
        key, payload = d["request_key"], d["payload"]
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("fv-forward-outcome:" + key,))
        if research_store.get(conn, OUTCOME_KIND, key):
            conn.commit()
            continue
        if not timing_valid(d):
            research_store.save(conn, OUTCOME_KIND, key, {
                "status": "INVALID_PROSPECTIVE_TIMING", "forward_id": FORWARD_ID,
                "decision_session": payload["decision_session"], "decision_record_id": d["record_id"]})
            conn.commit()
            invalid.append(payload["decision_session"])
            continue
        tickers = [r["ticker"] for r in payload["rows"] if r["side"] != "NO_DECISION"]
        due = payload["due_session"]
        prices = {t: db.fetch_prices(conn, t, completed) for t in tickers}
        result = score_month(payload, prices=prices, spy=db.fetch_prices(conn, "SPY", completed),
                             completed=completed)
        if result["status"] == "PENDING":
            _save_check(conn, f"{key}:{completed}", {"decision_session": payload["decision_session"],
                                                     "status": "OUTCOME_BLOCKED_MISSING_BARS",
                                                     "blocked": result["blocked"], "due_session": due})
            pending.append(payload["decision_session"])
        else:
            research_store.save(conn, OUTCOME_KIND, key, {**result, "decision_record_id": d["record_id"],
                                                          "decision_content_hash": d["content_hash"]})
            scored.append(payload["decision_session"])
        conn.commit()
    return {"scored": scored, "blocked": pending, "invalid": invalid}


def _register(conn) -> dict:
    from .. import research_store
    if fv.PROTOCOL_SHA256 != REPLAY_PROTOCOL_SHA256:
        raise ProtocolChanged("REPLAY_PROTOCOL_CHANGED")
    try:
        return research_store.save(conn, PROTOCOL_KIND, FORWARD_ID, PROTOCOL)
    except ValueError as exc:
        raise ProtocolChanged("FORWARD_PROTOCOL_CHANGED") from exc


class ProtocolChanged(RuntimeError):
    pass


def _freeze(conn, read_conn, session: str, now: datetime, protocol: dict) -> dict:
    from .. import research_store
    key = f"{FORWARD_ID}:{session}"
    existing = research_store.get(conn, DECISION_KIND, key)
    if existing:
        return {"status": "REPLAYED", "decision_session": session, "record_id": existing["record_id"]}
    entry, _ = windows(session)
    deadline = session_open(entry)
    if now >= deadline:
        # Recorded once; later runs report the recorded miss without failing again.
        miss_key = f"{key}:FREEZE_MISSED"
        if research_store.get(conn, CHECK_KIND, miss_key):
            return {"status": "MISSED_ALREADY_RECORDED", "decision_session": session}
        _save_check(conn, miss_key, {"decision_session": session, "status": "FREEZE_MISSED",
                                     "deadline": deadline.isoformat()})
        conn.commit()
        return {"status": "FREEZE_MISSED", "decision_session": session}
    universe_tickers = PROTOCOL["universe"]
    read_conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    missing = bars_missing(read_conn, session, [*universe_tickers, "SPY"])
    final_attempt = deadline - now <= timedelta(hours=FINAL_ATTEMPT_HOURS)
    if "SPY" in missing or (missing and not final_attempt):
        return {"status": "FREEZE_PENDING_DATA", "decision_session": session, "missing": missing,
                "final_attempt": final_attempt}
    universe, spy, regime_for = fv.load(read_conn, universe_tickers)
    rows = decide_month(universe, session, spy=spy, regime_for=regime_for)
    payload = decision_payload(session, rows, protocol=protocol, created_at=now, missing=missing)
    saved = research_store.save(conn, DECISION_KIND, key, payload)
    conn.commit()
    return {"status": "FROZEN", "decision_session": session, "record_id": saved["record_id"],
            "content_hash": saved["content_hash"], "counts": payload["counts"],
            "missing_bars_at_freeze": payload["missing_bars_at_freeze"]}


def run_daily(*, now: datetime | None = None) -> dict:
    """Score matured months, freeze the month-end if due, evaluate when ready."""
    from .. import db, research_store
    from ..admin_panel.store import audit

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("FORWARD_TIMESTAMP_NOT_AWARE")
    completed = latest_completed_session(now)
    receipt = {"stage": "fair_value_forward", "forward_id": FORWARD_ID, "completed_session": completed,
               "promotion": "NOT_AUTHORIZED", "broker_submission": False}
    with db.connect() as lock:
        lock.autocommit = True
        if not lock.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (FORWARD_ID,)).fetchone()[0]:
            return {**receipt, "status": "BUSY"}
        try:
            audit(lock, ACTOR, "FAIR_VALUE_FORWARD_STAGE_STARTED", {**receipt, "status": "STARTED"})
            with db.connect() as conn, db.connect() as read_conn:
                protocol = _register(conn)
                conn.commit()
                outcome_stats = score_pending(conn, completed)
                freeze = {"status": "NOTHING_DUE"}
                session = expected_decision_session(completed)
                if session:
                    freeze = _freeze(conn, read_conn, session, now, protocol)
                    read_conn.rollback()
                evaluation = evaluate(outcomes(conn))
                if evaluation["status"] != "PENDING_EVIDENCE" and not research_store.get(
                        conn, EVALUATION_KIND, f"{FORWARD_ID}:final"):
                    research_store.save(conn, EVALUATION_KIND, f"{FORWARD_ID}:final",
                                        clean({"forward_id": FORWARD_ID, **evaluation}))
                    conn.commit()
            failing = freeze["status"] in ("FREEZE_MISSED", "FREEZE_PENDING_DATA") or outcome_stats["invalid"]
            receipt.update(status="ATTENTION" if failing else "OK", protocol_sha256=PROTOCOL_SHA256,
                           protocol_record_id=protocol["record_id"], freeze=freeze, outcomes=outcome_stats,
                           evaluation={k: evaluation.get(k) for k in ("status", "months", "active_months",
                                                                       "mean", "lower_95", "upper_95")})
        except ProtocolChanged as exc:
            receipt.update(status="PROTOCOL_CHANGED", reason_code=str(exc))
        except Exception as exc:
            receipt.update(status="FAILED", reason_code=type(exc).__name__)
        audit(lock, ACTOR, "FAIR_VALUE_FORWARD_STAGE_FINISHED", clean(receipt))
        lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (FORWARD_ID,))
        return receipt


def summary(conn, *, now: datetime | None = None) -> dict:
    """Read-only status for the API, admin view and watchdog."""
    now = now or datetime.now(timezone.utc)
    conn.execute("SET TRANSACTION READ ONLY")
    completed = latest_completed_session(now)
    protocol = conn.execute("SELECT record_id,content_hash FROM research_evidence_records WHERE kind=%s AND "
                            "request_key=%s", (PROTOCOL_KIND, FORWARD_ID)).fetchone()
    frozen = decisions(conn)
    scored = outcomes(conn)
    expected = expected_decision_session(completed)
    frozen_sessions = {d["payload"]["decision_session"] for d in frozen}
    missing = bool(expected) and expected not in frozen_sessions
    deadline = session_open(windows(expected)[0]) if expected else None
    due_unscored = [d["payload"]["decision_session"] for d in frozen
                    if d["payload"]["due_session"] <= completed
                    and d["payload"]["decision_session"] not in {o.get("decision_session") for o in scored}]
    evaluation = evaluate(scored)
    return clean({
        "status": "OK", "forward_id": FORWARD_ID, "protocol_registered": bool(protocol),
        "protocol_sha256": PROTOCOL_SHA256, "first_decision_month": FIRST_DECISION_MONTH,
        "completed_session": completed, "expected_decision_session": expected,
        "freeze_missing": missing and deadline is not None and now >= deadline,
        "freeze_window_open": missing and deadline is not None and now < deadline,
        "freeze_deadline": deadline.isoformat() if deadline else None,
        "frozen_months": [{"decision_session": d["payload"]["decision_session"],
                           "due_session": d["payload"]["due_session"], "counts": d["payload"]["counts"],
                           "content_hash": d["content_hash"]} for d in frozen],
        "missed_months": sorted({c["payload"]["decision_session"] for c in checks(conn, 200)
                                 if c["payload"].get("status") == "FREEZE_MISSED"}),
        "matured_due_unscored": due_unscored,
        "next_due_session": min((d["payload"]["due_session"] for d in frozen
                                 if d["payload"]["decision_session"] not in
                                 {o.get("decision_session") for o in scored}), default=None),
        "invalid_months": [o["decision_session"] for o in scored if o.get("status") == "INVALID_PROSPECTIVE_TIMING"],
        "evaluation": evaluation,
        "promotion": "NOT_AUTHORIZED", "broker_submission": False,
    })
