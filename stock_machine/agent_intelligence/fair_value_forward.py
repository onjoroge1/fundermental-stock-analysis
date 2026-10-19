"""Forward tracking of the sector P/E fair-value rule: a confirmatory test.

The replay (fair-value-convergence.v1, docs/evidence/fair-value-replay-2026-10-18.md)
found one positive but not significant policy, "sector_timed_120". This module
tests that single policy on data the replay never saw:

- Freeze: at each month-end session S, before the open of the next session,
  the decision for every stock in the frozen universe is computed with the
  replay's own code (fair_value_replay.observe_session / sector_gaps /
  decision_row) and stored once as an append-only record. The freeze runs
  before any scoring, so a scoring problem can never cost a month.
- Score: once the 120-session window frozen with the decision has completed,
  each stock is scored with reward v5 from one price snapshot; FLAT scores
  zero. A stock whose window still lacks bars RETRY_SESSIONS sessions after it
  is due is excluded, visibly.
- Evaluate once, on a data-defined sample: the first 24 months with an active
  position in decision order (Newey-West lag 6, t(n-1) 97.5%), written only
  when every earlier frozen month has a final outcome. That record is final;
  later months are monitoring only. A pass only authorizes review.

Nothing here trades, sizes positions or changes paper or live settings.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from calendar import monthrange
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from math import ceil, sin
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
STICKY_DAYS = 7
# The replay protocol this tracker confirms, and the pinned behaviour of the
# shared decision code on a fixed synthetic case. Both are part of PROTOCOL, so
# changing either changes the registered protocol and needs a new FORWARD_ID.
REPLAY_PROTOCOL_SHA256 = "4b151cdfc7777f8adbc73eafbd51a6f98ead5c601b6788b4faf47b727ce0bda3"
SCORING_BEHAVIOR_SHA256 = "bca0081f5cbc56ad7830180665f2b6986a8ccac9a9e15529bb45120d59503203"
REWARD_VERSION = "risk-scaled-residual-paper-reward.v5"
DECISION_BEHAVIOR_SHA256 = "1ed3c3ba1a0ccc2c07e302659f2c7a3d999a6cc00d132d4cb039f95c222be9ea"

PROTOCOL_KIND = "FV_FORWARD_PROTOCOL_V1"
DECISION_KIND = "FV_FORWARD_DECISION_V1"
OUTCOME_KIND = "FV_FORWARD_OUTCOME_V1"
CHECK_KIND = "FV_FORWARD_CHECK_V1"
EVALUATION_KIND = "FV_FORWARD_EVALUATION_V1"
ACTOR = "fair-value-scheduler"
LOSS_STATUSES = ("FREEZE_MISSED", "FREEZE_LATE", "INVALID_PROSPECTIVE_TIMING", "DECISION_SOURCES_CHANGED")

# Code the decision depends on. Reviewed data files are data, refreshed by design;
# db.py is excluded because every schema migration edits its version constant
# (its read functions are covered by the behaviour pin's inputs, not its text).
DECISION_SOURCES = (
    "backtest/fair_value_replay.py", "backtest/direction_replay.py", "backtest/engine.py",
    "agent_intelligence/features.py", "agent_intelligence/state.py", "agent_intelligence/reward.py",
    "features/metrics.py", "features/scoring.py", "regime.py", "financial_integrity.py",
    "normalization/earnings_releases.py", "market_calendar.py",
)
FEATURES_KEPT = ("beta_63_vs_spy", "realized_vol_20", "realized_vol_60", "momentum_63",
                 "relative_momentum_63_vs_spy", "price")


class ProtocolChanged(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _protocol() -> dict:
    return {
        "forward_id": FORWARD_ID,
        "confirms": {"protocol_id": fv.PROTOCOL["protocol_id"], "protocol_sha256": REPLAY_PROTOCOL_SHA256,
                     "policy": POLICY, "evidence": "docs/evidence/fair-value-replay-2026-10-18.md"},
        "policy": {"fair_value": fv.PROTOCOL["fair_value"]["sector"], "solid_company": fv.PROTOCOL["solid_company"],
                   "trade": fv.PROTOCOL["trade"], "window_sessions": HORIZON},
        "universe": sorted(AGENT_UNIVERSE),
        "decision_session": "last XNYS session of each calendar month, from " + FIRST_DECISION_MONTH,
        "freeze_deadline": "before the open of the session after the decision session, checked against the clock "
                           "immediately before the record is written and against the database recording time in "
                           "the same transaction; a month not frozen in time is recorded FREEZE_MISSED (or "
                           "FREEZE_LATE) and is never reconstructed",
        "data_rule": "freeze when every universe stock and SPY has a bar at the decision session; on a final attempt "
                     f"(within {FINAL_ATTEMPT_HOURS}h of the deadline) freeze anyway and record stocks without a bar "
                     "as NO_DECISION_MISSING_BAR; without a SPY bar the month is never frozen and becomes FREEZE_MISSED",
        "window": f"entry at the close of the session after the decision session; exit {HORIZON} sessions later; "
                  "both dates are frozen with the decision",
        "reward": "risk-scaled-residual-paper-reward.v5 from one price snapshot read at scoring; FLAT = 0",
        "missing_bars": f"retry once per session; {RETRY_SESSIONS} sessions after the due session a stock still "
                        "without a complete window is EXCLUDED_MISSING_BARS and omitted from that month's mean",
        "evaluation": f"evaluated once, on every scored month in decision order up to and including the "
                      f"{MIN_ACTIVE_MONTHS}th month with an active position, and only when every earlier frozen "
                      "month has a final outcome and scoring had no error in that run: monthly mean of scored stocks "
                      "(FLAT = 0); Newey-West lag ceil(120/21) = 6; Student t(n-1) 97.5%; "
                      "PASS_REQUIRES_INDEPENDENT_REVIEW if the mean and its lower 95% bound are both positive, "
                      "else NOT_SUPPORTED; that record is final and later months are monitoring only",
        "single_hypothesis": "one pre-specified policy is confirmed, so no multiple-testing correction applies",
        "timing_validity": "a decision created or recorded at or after its frozen deadline is "
                           "INVALID_PROSPECTIVE_TIMING and never scored",
        "decision_behavior": "the shared decision code must reproduce decision_behavior_sha256 on the fixed synthetic "
                             "case or no month is frozen; changes to the fundamentals pipeline (engine, metrics, "
                             "scoring) are tracked by a source fingerprint and recorded as DECISION_SOURCES_CHANGED",
        "decision_behavior_sha256": DECISION_BEHAVIOR_SHA256,
        "scoring_behavior": f"scoring must use reward version {REWARD_VERSION} and reproduce scoring_behavior_sha256 "
                            "on a fixed synthetic window, or no month is scored (the freeze is unaffected)",
        "scoring_behavior_sha256": SCORING_BEHAVIOR_SHA256,
        "qualification": "PASS_REQUIRES_INDEPENDENT_REVIEW; promotion NOT_AUTHORIZED; broker_submission false",
    }


PROTOCOL = _protocol()
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


def decision_sessions_through(completed: str) -> list[str]:
    """Every decision session from the first month up to the expected one, oldest first."""
    last = expected_decision_session(completed)
    out, year, month = [], int(FIRST_DECISION_MONTH[:4]), int(FIRST_DECISION_MONTH[5:])
    while last:
        session = month_end_session(f"{year:04d}-{month:02d}-01")
        if session > last:
            break
        out.append(session)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


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


# ---- decisions -------------------------------------------------------------------

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


def _fixture_days(start: date, count: int) -> list[str]:
    """Plain weekdays: the fixture must not depend on exchange-calendar releases."""
    out, day = [], start
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


# (sector, P/E, profitability, financial health, daily drift, history sessions)
# Alpha trends down, Beta and Gamma up, so timing agrees with shorts in Alpha
# and longs elsewhere and disagrees otherwise. P/Es are lopsided within a
# sector (median != mean); Gamma has too few names and uses the universe
# median; quality scores sit on both sides of 50; one stock has too little
# history (NO_DECISION) and one has losses (no P/E).
FIXTURE = (
    ("Alpha", 40, 40, 60, -0.0042, 260), ("Alpha", 38, 60, 60, -0.0035, 260), ("Alpha", 12, 60, 60, -0.0028, 260),
    ("Alpha", 15, 60, 45, -0.0021, 260), ("Alpha", 44, 60, 60, -0.0014, 260), ("Alpha", 14, 60, 60, -0.0007, 260),
    ("Beta", 9, 40, 60, 0.0012, 260), ("Beta", 26, 60, 60, 0.0019, 260), ("Beta", 11, 60, 60, 0.0026, 260),
    ("Beta", 24, 60, 60, 0.0033, 260), ("Beta", 8, 60, 60, 0.0040, 260), ("Beta", 30, 60, 60, 0.0047, 260),
    ("Gamma", 10, 60, 60, 0.0030, 260), ("Gamma", 35, 60, 60, 0.0030, 260),
    ("Beta", 9, 60, 60, 0.0030, 40), ("Beta", -5.0, 60, 60, 0.0030, 260),
    # Delta (rising, median 20): gaps +0.205 (LONG) and +0.195 (FLAT) straddle +20%.
    ("Delta", 20, 60, 60, 0.0030, 260), ("Delta", 20, 60, 60, 0.0030, 260), ("Delta", 20, 60, 60, 0.0030, 260),
    ("Delta", 20 / 1.205, 60, 60, 0.0030, 260), ("Delta", 20 / 1.195, 60, 60, 0.0030, 260),
    # Epsilon (falling, median 20): gaps -0.205 (SHORT) and -0.195 (FLAT) straddle -20%.
    ("Epsilon", 20, 60, 60, -0.0030, 260), ("Epsilon", 20, 60, 60, -0.0030, 260),
    ("Epsilon", 20, 60, 60, -0.0030, 260), ("Epsilon", 20 / 0.795, 60, 60, -0.0030, 260),
    ("Epsilon", 20 / 0.805, 60, 60, -0.0030, 260),
    # Theta (median 30): quality exactly 50 (solid) and 49.9 (not); a weak agreeing
    # technical signal (LONG) and a weak disagreeing one (FLAT).
    ("Theta", 10, 50, 60, 0.0030, 260), ("Theta", 10, 49.9, 60, 0.0030, 260),
    ("Theta", 10, 60, 60, 0.0004, 260), ("Theta", 10, 60, 60, 0.00035, 260),
    ("Theta", 30, 60, 60, 0.0030, 260), ("Theta", 30, 60, 60, 0.0030, 260), ("Theta", 30, 60, 60, 0.0030, 260),
    ("Theta", 30, 60, 60, 0.0030, 260), ("Theta", 30, 60, 60, 0.0030, 260),
    # Zeta has exactly 4 names (its own median, 25), Eta 3 (the universe median);
    # both differ from the universe median so a sector-size change moves their gaps.
    ("Zeta", 10, 60, 60, 0.0030, 260), ("Zeta", 10, 60, 60, 0.0030, 260),
    ("Zeta", 40, 60, 60, 0.0030, 260), ("Zeta", 40, 60, 60, 0.0030, 260),
    ("Eta", 10, 60, 60, 0.0030, 260), ("Eta", 10, 60, 60, 0.0030, 260), ("Eta", 40, 60, 60, 0.0030, 260),
)


def behavior_fixture():
    days = _fixture_days(date(2024, 1, 2), 260)
    session = days[-1]

    def series(drift, wobble, start, length):
        rows, price = [], start
        for i, day in enumerate(days[-length:]):
            price *= 1 + drift + wobble * sin(i / 3)
            rows.append({"date": day, "open": price, "high": price * 1.01, "low": price * 0.99,
                         "close": price, "adj_close": price, "volume": 1_000_000})
        return rows

    universe = {}
    for i, (sector, pe, profit, health, drift, length) in enumerate(FIXTURE):
        prices = series(drift, 0.01, 40 + 5 * (i % 16), length)
        closes = {r["date"] for r in prices}

        def observe(as_of, have=closes, ratio=pe, p=profit, h=health):
            return {"composite": 70, "detail": {"pe_ttm": ratio if as_of in have else None,
                                                "profitability_score": p, "financial_health_score": h}}
        universe[f"S{i:02d}"] = {"prices": prices, "sector_rows": None, "observe": observe, "sector": sector}
    return universe, series(0.0004, 0.004, 400.0, 260), session


def behavior_digest() -> str:
    """Rounded decisions and scoring inputs on the fixture (platform float noise cannot move it)."""
    def r6(x):
        return None if x is None else round(float(x), 6)
    universe, spy, session = behavior_fixture()
    rows = decide_month(universe, session, spy=spy)
    return digest([{"ticker": r["ticker"], "side": r["side"], "solid": r.get("solid"), "pe": r6(r.get("pe_ttm")),
                    "gap": r6(r.get("sector_gap")), "tech": r6(r.get("technical_signal")),
                    "features": {k: r6(v) for k, v in sorted((r.get("features") or {}).items())}}
                   for r in rows])


def scoring_digest() -> str:
    """Reward v5 on a fixed synthetic window for a few frozen-feature cases (rounded)."""
    from . import reward
    days = _fixture_days(date(2024, 1, 2), 260)
    entry, due = days[100], days[100 + HORIZON]

    def path(drift, start):
        out, price = {}, start
        for i, day in enumerate(days):
            price *= 1 + drift + 0.01 * sin(i / 3)
            out[day] = price
        return out
    stock, spy = path(0.0012, 50.0), path(0.0004, 400.0)
    cases = [{"beta_63_vs_spy": 0.5, "realized_vol_20": 0.3}, {"beta_63_vs_spy": 1.4, "realized_vol_20": None,
             "realized_vol_60": 0.25}, {"beta_63_vs_spy": None}, {"beta_63_vs_spy": 4.0, "realized_vol_20": 0.01}]
    out = [{k: round(v, 9) for k, v in dr.rewards_between(entry, due, {"features": c}, stock=stock, spy=spy,
                                                         horizon=HORIZON)["rewards"].items()} for c in cases]
    return digest({"version": reward.VERSION, "rewards": out})


def decision_payload(session: str, rows: list[dict], *, protocol: dict, created_at: datetime,
                     missing: list[str], deadline: datetime | None = None) -> dict:
    entry, due = windows(session)
    deadline = deadline or session_open(entry)
    for r in rows:
        if r["ticker"] in missing:
            r.update(side="NO_DECISION", reason="NO_DECISION_MISSING_BAR")
    counts = {s: sum(r["side"] == s for r in rows) for s in ("LONG", "SHORT", "FLAT", "NO_DECISION")}
    return {
        "forward_id": FORWARD_ID, "protocol_record_id": protocol["record_id"],
        "protocol_content_hash": protocol["content_hash"], "decision_session": session,
        "entry_session": entry, "due_session": due, "freeze_deadline": deadline.isoformat(),
        "created_at": created_at.isoformat(),
        "code": {"git_sha": os.getenv("GITHUB_SHA"), "decision_sources_sha256": source_fingerprint(),
                 "decision_behavior_sha256": DECISION_BEHAVIOR_SHA256},
        "missing_bars_at_freeze": sorted(missing), "counts": counts, "rows": rows,
        "promotion": "NOT_AUTHORIZED", "broker_submission": False,
    }


# ---- timing ----------------------------------------------------------------------

def _parse(stamp: str) -> datetime:
    text = str(stamp).replace("Z", "+00:00").replace(" ", "T")
    if re.search(r"[+-]\d\d$", text):  # Postgres prints "+00"; older Pythons need "+00:00"
        text += ":00"
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _recorded_at(record: dict) -> datetime:
    return _parse(record["recorded_at"])


def _deadline(payload: dict) -> datetime:
    """The deadline frozen with the decision (a later calendar revision cannot move it)."""
    frozen = payload.get("freeze_deadline")
    return _parse(frozen) if frozen else session_open(payload["entry_session"])


def timing_valid(decision: dict) -> bool:
    payload = decision["payload"]
    deadline = _deadline(payload)
    return _parse(payload["created_at"]) < deadline and _recorded_at(decision) < deadline


# ---- scoring ---------------------------------------------------------------------

def score_month(decision_payload_: dict, *, prices: dict, spy: list[dict], completed: str) -> dict:
    """Outcome for one frozen month, or {"status": "PENDING", "blocked": [...]}."""
    session = decision_payload_["decision_session"]
    entry, due = decision_payload_["entry_session"], decision_payload_["due_session"]
    spy_adj = dr._adjusted(spy)
    expired = completed >= session_offset(due, RETRY_SESSIONS)
    results, blocked = [], []
    for r in decision_payload_["rows"]:
        if r["side"] == "NO_DECISION":
            results.append({"ticker": r["ticker"], "status": "NO_DECISION", "side": "NO_DECISION"})
            continue
        stock_adj = dr._adjusted(prices.get(r["ticker"]) or [])
        outcome = dr.rewards_between(entry, due, {"features": r["features"]}, stock=stock_adj, spy=spy_adj,
                                     horizon=HORIZON)
        if outcome is None:
            if expired:
                results.append({"ticker": r["ticker"], "status": "EXCLUDED_MISSING_BARS", "side": r["side"]})
            else:
                blocked.append(r["ticker"])
            continue
        path = [stock_adj.get(d) for d in session_dates(entry, due)]
        score = 0.0 if r["side"] == "FLAT" else float(outcome["rewards"][r["side"]])
        results.append({"ticker": r["ticker"], "status": "SCORED", "side": r["side"], "score": score,
                        "rewards": outcome["rewards"], "beta": outcome["beta"], "path_sha256": digest(path)})
    if blocked:
        return {"status": "PENDING", "blocked": sorted(blocked)}
    scored = [x for x in results if x["status"] == "SCORED"]
    return clean({
        "status": "SCORED", "forward_id": FORWARD_ID, "decision_session": session,
        "entry_session": entry, "due_session": due, "scored_through": completed, "results": results,
        "monthly_mean": mean(x["score"] for x in scored) if scored else None,
        "active": any(x["side"] in ("LONG", "SHORT") for x in scored),
        "scored": len(scored), "excluded": sum(x["status"] == "EXCLUDED_MISSING_BARS" for x in results),
        "spy_path_sha256": digest([spy_adj.get(d) for d in session_dates(entry, due)]),
        "reward_version": REWARD_VERSION, "scoring_behavior_sha256": SCORING_BEHAVIOR_SHA256,
    })


# ---- evaluation ------------------------------------------------------------------

def evaluate(outcomes: list[dict]) -> dict:
    """The registered statistic on the data-defined sample: scored months in decision
    order up to and including the MIN_ACTIVE_MONTHS-th active month."""
    months = sorted((o for o in outcomes if o.get("status") == "SCORED" and o.get("monthly_mean") is not None),
                    key=lambda o: o["decision_session"])
    active, sample = 0, []
    for o in months:
        sample.append(o)
        active += bool(o["active"])
        if active >= MIN_ACTIVE_MONTHS:
            break
    values = [o["monthly_mean"] for o in sample]
    out = {"months": len(values), "active_months": active, "min_active_months": MIN_ACTIVE_MONTHS,
           "mean": mean(values) if values else None,
           "through_session": sample[-1]["decision_session"] if sample else None,
           "scored_months_available": len(months)}
    if active < MIN_ACTIVE_MONTHS or len(values) < 3:
        return {**out, "status": "PENDING_EVIDENCE"}
    se = inference.newey_west_se(values, lag=ceil(HORIZON / 21))
    t = inference.t_quantile_975(len(values) - 1)
    lower, upper = out["mean"] - t * se, out["mean"] + t * se
    passed = out["mean"] > 0 and lower > 0
    return {**out, "newey_west_se": se, "lower_95": lower, "upper_95": upper,
            "status": "PASS_REQUIRES_INDEPENDENT_REVIEW" if passed else "NOT_SUPPORTED",
            "promotion": "NOT_AUTHORIZED"}


def reported_evaluation(final: dict | None, live: dict) -> dict:
    """The final record once it exists. Before that a reached threshold is only
    EVALUATION_DUE (no verdict shown); afterwards later numbers are monitoring."""
    if final:
        monitoring = {k: live.get(k) for k in ("scored_months_available", "through_session")}
        return {**final, "final": True, "post_final_monitoring": monitoring}
    if live["status"] == "PENDING_EVIDENCE":
        return {**live, "final": False}
    return {"status": "EVALUATION_DUE", "final": False,
            **{k: live.get(k) for k in ("months", "active_months", "through_session")}}


# ---- database reads --------------------------------------------------------------

_COLS = ("request_key", "record_id", "content_hash", "payload", "recorded_at")


def _rows(conn, sql, params):
    return [dict(zip(_COLS, r)) for r in conn.execute(sql, params).fetchall()]


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


def checks_with_status(conn, statuses) -> list[dict]:
    return _rows(conn, """SELECT request_key,record_id,content_hash,payload,recorded_at::text
        FROM research_evidence_records WHERE kind=%s AND payload->>'forward_id'=%s
          AND payload->>'status'=ANY(%s) ORDER BY payload->>'decision_session'""",
                 (CHECK_KIND, FORWARD_ID, list(statuses)))


def final_evaluation(conn) -> dict | None:
    row = conn.execute("SELECT payload FROM research_evidence_records WHERE kind=%s AND request_key=%s",
                       (EVALUATION_KIND, f"{FORWARD_ID}:final")).fetchone()
    return row[0] if row else None


def sticky_attention(conn, now: datetime) -> list[str]:
    """Losses that became known (or were recorded) in the last STICKY_DAYS, so the
    daily watchdog sees them even though later runs succeed."""
    rows = _rows(conn, """SELECT request_key,record_id,content_hash,payload,recorded_at::text
        FROM research_evidence_records WHERE kind=ANY(%s) AND payload->>'forward_id'=%s
          AND payload->>'status'=ANY(%s) ORDER BY payload->>'decision_session'""",
                 ([CHECK_KIND, OUTCOME_KIND], FORWARD_ID, list(LOSS_STATUSES)))
    out = []
    for r in rows:
        known = r["payload"].get("deadline") or r["payload"].get("due_session")
        known_at = _parse(known if "T" in (known or "") else f"{known}T21:00:00+00:00") if known else None
        recent = [t for t in (known_at, _recorded_at(r))
                  if t is not None and timedelta(0) <= now - t <= timedelta(days=STICKY_DAYS)]
        if recent:
            out.append(f"{r['payload']['status']}:{r['payload']['decision_session']}")
    return out


def bars_missing(conn, session: str, tickers) -> list[str]:
    have = {r[0] for r in conn.execute(
        "SELECT ticker FROM prices_daily WHERE date=%s AND ticker=ANY(%s) AND adj_close > 0",
        (session, list(tickers))).fetchall()}
    return sorted(set(tickers) - have)


# ---- database writes -------------------------------------------------------------

def _save_check(conn, key: str, payload: dict) -> bool:
    """Write a check once per key (no commit); False when it already exists."""
    from .. import research_store
    if research_store.get(conn, CHECK_KIND, key):
        return False
    research_store.save(conn, CHECK_KIND, key, clean({"forward_id": FORWARD_ID, **payload}))
    return True


def _record_loss(conn, session: str, deadline: datetime, status: str = "FREEZE_MISSED", **extra) -> bool:
    written = _save_check(conn, f"{FORWARD_ID}:{session}:{status}",
                          {"decision_session": session, "status": status, "deadline": deadline.isoformat(), **extra})
    conn.commit()
    return written


def score_pending(conn, read_conn, completed: str) -> dict:
    """Score every due month; an error in one month never stops the others."""
    from .. import db, research_store
    stats = {"scored": [], "blocked": [], "invalid": [], "errors": []}
    for d in unscored_due(conn, completed):
        key, payload = d["request_key"], d["payload"]
        try:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("fv-forward-outcome:" + key,))
            if research_store.get(conn, OUTCOME_KIND, key) or research_store.get(
                    conn, CHECK_KIND, f"{key}:{completed}"):
                conn.commit()  # scored already, or already checked and blocked this session
                continue
            if not timing_valid(d):
                research_store.save(conn, OUTCOME_KIND, key, {
                    "status": "INVALID_PROSPECTIVE_TIMING", "forward_id": FORWARD_ID,
                    "decision_session": payload["decision_session"], "due_session": payload["due_session"],
                    "decision_record_id": d["record_id"]})
                conn.commit()
                stats["invalid"].append(payload["decision_session"])
                continue
            tickers = [r["ticker"] for r in payload["rows"] if r["side"] != "NO_DECISION"]
            read_conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            prices = {t: db.fetch_prices(read_conn, t, completed) for t in tickers}
            spy = db.fetch_prices(read_conn, "SPY", completed)
            read_conn.rollback()
            result = score_month(payload, prices=prices, spy=spy, completed=completed)
            if result["status"] == "PENDING":
                _save_check(conn, f"{key}:{completed}", {"decision_session": payload["decision_session"],
                                                         "status": "OUTCOME_BLOCKED_MISSING_BARS",
                                                         "blocked": result["blocked"],
                                                         "due_session": payload["due_session"]})
                stats["blocked"].append(payload["decision_session"])
            else:
                research_store.save(conn, OUTCOME_KIND, key, {**result, "decision_record_id": d["record_id"],
                                                              "decision_content_hash": d["content_hash"]})
                stats["scored"].append(payload["decision_session"])
            conn.commit()
        except Exception as exc:  # this month retries next run
            for c in (conn, read_conn):
                with suppress(Exception):
                    c.rollback()
            stats["errors"].append({"decision_session": payload.get("decision_session"),
                                    "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    return stats


def _register(conn) -> dict:
    from .. import research_store
    if fv.PROTOCOL_SHA256 != REPLAY_PROTOCOL_SHA256:
        raise ProtocolChanged("REPLAY_PROTOCOL_CHANGED")
    try:
        return research_store.save(conn, PROTOCOL_KIND, FORWARD_ID, PROTOCOL)
    except ValueError as exc:
        raise ProtocolChanged("FORWARD_PROTOCOL_CHANGED") from exc


def _freeze(conn, read_conn, session: str, now: datetime, clock, protocol: dict) -> dict:
    from .. import research_store
    key = f"{FORWARD_ID}:{session}"
    existing = research_store.get(conn, DECISION_KIND, key)
    if existing:
        if not timing_valid(existing):  # repair a late marker lost in a crash
            if _record_loss(conn, session, _deadline(existing["payload"]), "FREEZE_LATE"):
                return {"status": "FREEZE_LATE", "decision_session": session, "record_id": existing["record_id"]}
        conn.commit()
        return {"status": "REPLAYED", "decision_session": session, "record_id": existing["record_id"]}
    conn.commit()  # stay idle (not idle in transaction) during the load below
    entry, _ = windows(session)
    deadline = session_open(entry)
    if now >= deadline:
        written = _record_loss(conn, session, deadline)
        return {"status": "FREEZE_MISSED" if written else "MISSED_ALREADY_RECORDED", "decision_session": session}
    read_conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    missing = bars_missing(read_conn, session, [*PROTOCOL["universe"], "SPY"])
    final_attempt = deadline - now <= timedelta(hours=FINAL_ATTEMPT_HOURS)
    if "SPY" in missing or (missing and not final_attempt):
        read_conn.rollback()
        return {"status": "FREEZE_PENDING_DATA", "decision_session": session, "missing": missing,
                "final_attempt": final_attempt}
    universe, spy, regime_for = fv.load(read_conn, PROTOCOL["universe"])
    rows = decide_month(universe, session, spy=spy, regime_for=regime_for)
    read_conn.rollback()
    created = clock()
    if created >= deadline:  # the load ran past the open: never write a decision that is already late
        written = _record_loss(conn, session, deadline)
        return {"status": "FREEZE_MISSED" if written else "MISSED_ALREADY_RECORDED", "decision_session": session}
    payload = decision_payload(session, rows, protocol=protocol, created_at=created, missing=missing,
                               deadline=deadline)
    saved = research_store.save(conn, DECISION_KIND, key, payload)
    late = _recorded_at(saved) >= deadline
    if late:  # same transaction as the decision, so the marker cannot be lost
        _save_check(conn, f"{key}:FREEZE_LATE", {"decision_session": session, "status": "FREEZE_LATE",
                                                 "deadline": deadline.isoformat()})
    sources = source_fingerprint()
    earlier = [d for d in decisions(conn) if d["payload"]["decision_session"] < session]
    first = (earlier[0]["payload"].get("code") or {}).get("decision_sources_sha256") if earlier else sources
    changed = first != sources
    if changed:
        _save_check(conn, f"{key}:DECISION_SOURCES_CHANGED",
                    {"decision_session": session, "status": "DECISION_SOURCES_CHANGED",
                     "deadline": deadline.isoformat(), "first_sources_sha256": first, "sources_sha256": sources})
    conn.commit()
    return {"status": "FREEZE_LATE" if late else "FROZEN", "decision_session": session,
            "record_id": saved["record_id"], "content_hash": saved["content_hash"], "counts": payload["counts"],
            "missing_bars_at_freeze": payload["missing_bars_at_freeze"], "decision_sources_changed": changed}


def freeze_due(conn, read_conn, completed: str, now: datetime, clock, protocol: dict) -> dict:
    """Record any earlier month-end that was never frozen, then freeze the latest if due.

    Months are matched by calendar month, so a revised calendar cannot record one twice."""
    sessions = decision_sessions_through(completed)
    if not sessions:
        return {"status": "NOTHING_DUE", "newly_missed": []}
    frozen = {d["payload"]["decision_session"][:7] for d in decisions(conn)}
    recorded = {c["payload"]["decision_session"][:7] for c in checks_with_status(conn, ("FREEZE_MISSED",))}
    newly_missed = []
    for session in sessions[:-1]:
        if session[:7] not in frozen and session[:7] not in recorded:
            if _record_loss(conn, session, session_open(windows(session)[0])):
                newly_missed.append(session)
    latest = sessions[-1]
    if latest[:7] in frozen and latest not in {d["payload"]["decision_session"] for d in decisions(conn)}:
        return {"status": "REPLAYED", "decision_session": latest, "newly_missed": newly_missed}
    if latest[:7] not in frozen and latest[:7] in recorded:
        return {"status": "MISSED_ALREADY_RECORDED", "decision_session": latest, "newly_missed": newly_missed}
    result = _freeze(conn, read_conn, sessions[-1], now, clock, protocol)
    return {**result, "newly_missed": newly_missed}


def _ready_for_final(conn, through: str) -> bool:
    done = {o.get("decision_session") for o in outcomes(conn)}
    return all(d["payload"]["decision_session"] in done for d in decisions(conn)
               if d["payload"]["decision_session"] <= through)


OK_FREEZE = ("NOTHING_DUE", "FROZEN", "REPLAYED", "MISSED_ALREADY_RECORDED")


def run_daily(*, now: datetime | None = None, clock=None) -> dict:
    """Freeze the month-end if due, then score matured months, then evaluate once."""
    from .. import db, research_store
    from ..admin_panel.store import audit

    if clock is None:
        clock = (lambda: now) if now is not None else _utcnow
    now = now or clock()
    if now.tzinfo is None:
        raise ValueError("FORWARD_TIMESTAMP_NOT_AWARE")
    completed = latest_completed_session(now)
    receipt = {"stage": "fair_value_forward", "forward_id": FORWARD_ID, "completed_session": completed,
               "promotion": "NOT_AUTHORIZED", "broker_submission": False}
    if os.getenv("GITHUB_REF") != "refs/heads/main":
        return {**receipt, "status": "REFUSED_NON_MAIN", "reason_code": os.getenv("GITHUB_REF") or "UNSET"}
    try:
        return _run_locked(receipt, completed, now, clock, db, research_store, audit)
    except Exception as exc:  # e.g. the lock connection itself failed: still return a receipt
        return {**receipt, "status": "FAILED", "reason_code": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _run_locked(receipt, completed, now, clock, db, research_store, audit) -> dict:
    from . import reward
    with db.connect() as lock:
        lock.autocommit = True
        if not lock.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (FORWARD_ID,)).fetchone()[0]:
            return {**receipt, "status": "BUSY"}
        try:
            audit(lock, ACTOR, "FAIR_VALUE_FORWARD_STAGE_STARTED", {**receipt, "status": "STARTED"})
            with db.connect() as conn, db.connect() as read_conn:
                protocol = _register(conn)
                conn.commit()
                receipt.update(protocol_sha256=PROTOCOL_SHA256, protocol_record_id=protocol["record_id"])
                if behavior_digest() != DECISION_BEHAVIOR_SHA256:
                    raise ProtocolChanged("DECISION_BEHAVIOR_CHANGED")
                freeze = freeze_due(conn, read_conn, completed, now, clock, protocol)
                receipt["freeze"] = freeze
                try:
                    if reward.VERSION != REWARD_VERSION or scoring_digest() != SCORING_BEHAVIOR_SHA256:
                        raise ProtocolChanged("SCORING_BEHAVIOR_CHANGED")  # never blocks the freeze above
                    stats = score_pending(conn, read_conn, completed)
                except Exception as exc:  # e.g. the due-month query itself failed
                    for c in (conn, read_conn):
                        with suppress(Exception):
                            c.rollback()
                    stats = {"scored": [], "blocked": [], "invalid": [],
                             "errors": [{"error": f"{type(exc).__name__}: {str(exc)[:200]}"}]}
                receipt["outcomes"] = stats
                live = evaluate(outcomes(conn))
                final = final_evaluation(conn)
                if (not final and live["status"] != "PENDING_EVIDENCE" and not stats["errors"]
                        and _ready_for_final(conn, live["through_session"])):
                    research_store.save(conn, EVALUATION_KIND, f"{FORWARD_ID}:final",
                                        clean({"forward_id": FORWARD_ID, **live}))
                    conn.commit()
                    final = final_evaluation(conn)
                sticky = sticky_attention(conn, clock())
            report = reported_evaluation(final, live)
            attention = (freeze["status"] not in OK_FREEZE or freeze.get("newly_missed")
                         or freeze.get("decision_sources_changed") or stats["invalid"] or stats["errors"] or sticky)
            receipt.update(status="ATTENTION" if attention else "OK", sticky_attention=sticky,
                           evaluation={k: report.get(k) for k in ("status", "final", "months", "active_months",
                                                                   "mean", "lower_95", "upper_95")})
        except ProtocolChanged as exc:
            receipt.update(status="PROTOCOL_CHANGED", reason_code=str(exc))
        except Exception as exc:
            receipt.update(status="FAILED", reason_code=f"{type(exc).__name__}: {str(exc)[:200]}")
        finally:
            try:
                audit(lock, ACTOR, "FAIR_VALUE_FORWARD_STAGE_FINISHED",
                      clean({**receipt, "status": receipt.get("status", "FAILED")}))
            except Exception:
                receipt["audit_failed"] = True
                if receipt.get("status") == "OK":
                    receipt["status"] = "ATTENTION"
            with suppress(Exception):
                lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (FORWARD_ID,))
        return receipt


def summary(conn, *, now: datetime | None = None) -> dict:
    """Read-only status for the API, admin view and watchdog."""
    now = now or _utcnow()
    conn.execute("SET TRANSACTION READ ONLY")
    completed = latest_completed_session(now)
    protocol = conn.execute("SELECT record_id,content_hash FROM research_evidence_records WHERE kind=%s AND "
                            "request_key=%s", (PROTOCOL_KIND, FORWARD_ID)).fetchone()
    frozen = decisions(conn)
    scored = outcomes(conn)
    scored_sessions = {o.get("decision_session") for o in scored}
    frozen_months = {d["payload"]["decision_session"][:7]: d for d in frozen}
    sessions = decision_sessions_through(completed)
    expected = sessions[-1] if sessions else None
    expected_frozen = frozen_months.get(expected[:7]) if expected else None
    deadline = (_deadline(expected_frozen["payload"]) if expected_frozen
                else session_open(windows(expected)[0]) if expected else None)
    missing = bool(expected) and expected_frozen is None
    losses = {}
    for c in checks_with_status(conn, ("FREEZE_MISSED", "FREEZE_LATE")):
        losses.setdefault(c["payload"]["decision_session"][:7], c["payload"]["decision_session"])
    for missed in sessions[:-1]:
        if missed[:7] not in frozen_months:
            losses.setdefault(missed[:7], missed)
    fingerprints = sorted({(d["payload"].get("code") or {}).get("decision_sources_sha256") for d in frozen} - {None})
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
        "missed_months": sorted(losses.values()),
        "matured_due_unscored": [d["payload"]["decision_session"] for d in frozen
                                 if d["payload"]["due_session"] <= completed
                                 and d["payload"]["decision_session"] not in scored_sessions],
        "next_due_session": min((d["payload"]["due_session"] for d in frozen
                                 if d["payload"]["decision_session"] not in scored_sessions), default=None),
        "invalid_months": [o["decision_session"] for o in scored if o.get("status") == "INVALID_PROSPECTIVE_TIMING"],
        "decision_code_versions": len(fingerprints),
        "sticky_attention": sticky_attention(conn, now),
        "evaluation": reported_evaluation(final_evaluation(conn), evaluate(scored)),
        "promotion": "NOT_AUTHORIZED", "broker_submission": False,
    })
