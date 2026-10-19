"""Forward shadow of the pre-registered event strategies: the clean test.

The event replay (event-strategy-replay.v1) scores a development stage and a
pseudo-out-of-sample hold-out on years earlier research had already seen.
This module applies the same rules to sessions nobody has seen yet:

- Freeze: after each exchange session S closes and before the next session
  opens, every universe stock's entry flags at S's close are computed with the
  replay's own code (strategy_signals, event_replay.s3_reaction and
  _s4_eligibility) and stored once, append-only. S3 reads SEC 8-K item 2.02
  filings live from data.sec.gov with the rule that built the replay dataset.
- Trades: entry at the next session's close; one open trade per stock and
  strategy; exits and reward v5 as in the replay. A trade is recorded once its
  maximum-hold window has completed (so a missing bar excludes it exactly as in
  the replay), from one price snapshot. The stop distance and the S3 gap-fill
  level are frozen as ratios to the signal close, so a later dividend
  adjustment of the price history cannot move them.
- Evaluate once: trades signalled in the 24 calendar months EVALUATION_MONTHS,
  once every one of them is final. S1-S3 at family-wise 95% (Bonferroni, as in
  the replay's hold-out), S4 exploratory. A pass only authorizes review.

Records reuse the FV_FORWARD_* evidence kinds (migration 0028) under this
module's FORWARD_ID, and every query here filters on it, so the fair-value
tracker and this one never read each other's records. Nothing here trades,
sizes positions, or changes paper or live settings; the agent sees these
signals as information only.
"""
from __future__ import annotations

import hashlib
import os
import time
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..agents.contracts import AGENT_UNIVERSE, digest
from ..backtest import direction_replay as dr
from ..backtest import event_replay as er
from ..events import earnings_announcements as ea
from ..market_calendar import latest_completed_session, session_dates, session_offset
from . import fair_value_forward as fvf
from . import strategy_signals as ss

FORWARD_ID = "event-strategy-replay.v1:forward.v1"
FIRST_DECISION_SESSION = "2026-10-21"
EVALUATION_MONTHS = ("2026-11", "2028-10")
# The replay protocol this shadow confirms (pinned in tests/test_event_replay.py).
REPLAY_PROTOCOL_SHA256 = "3fa1d5cfeecca4b3248844f2b444fa71f33ec1422fefffb7fa675b25d82c6a0c"
RETRY_SESSIONS = 10
FINAL_ATTEMPT_HOURS = 4
STICKY_DAYS = 7
HISTORY_SESSIONS = 400
SEC_LOOKBACK_DAYS = 14

PROTOCOL_KIND = fvf.PROTOCOL_KIND
DECISION_KIND = fvf.DECISION_KIND
OUTCOME_KIND = fvf.OUTCOME_KIND
CHECK_KIND = fvf.CHECK_KIND
EVALUATION_KIND = fvf.EVALUATION_KIND
ACTOR = "strategy-forward-scheduler"
STRATEGIES = (*er.CONFIRMATORY, *er.EXPLORATORY)
LOSS_STATUSES = ("FREEZE_MISSED", "FREEZE_LATE", "INVALID_PROSPECTIVE_TIMING", "DECISION_SOURCES_CHANGED")
OK_FREEZE = ("NOTHING_DUE", "FROZEN", "REPLAYED", "MISSED_ALREADY_RECORDED")

# Code the flags depend on (db.py is excluded for the reason given in fair_value_forward).
DECISION_SOURCES = (
    "agent_intelligence/strategy_signals.py", "agent_intelligence/technical_setups.py",
    "agent_intelligence/features.py", "agent_intelligence/reward.py", "backtest/event_replay.py",
    "backtest/direction_replay.py", "backtest/fair_value_replay.py", "backtest/engine.py",
    "events/earnings_announcements.py", "features/metrics.py", "features/scoring.py", "financial_integrity.py",
    "normalization/earnings_releases.py", "data_quality.py", "market_calendar.py",
)
FEATURES_KEPT = fvf.FEATURES_KEPT


class ProtocolChanged(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _protocol() -> dict:
    return {
        "forward_id": FORWARD_ID,
        "confirms": {"protocol_id": er.PROTOCOL["protocol_id"], "protocol_sha256": REPLAY_PROTOCOL_SHA256},
        "strategies": er.PROTOCOL["strategies"], "exits": er.PROTOCOL["exits"], "timing": er.PROTOCOL["timing"],
        "strategy_behavior_sha256": er.STRATEGY_BEHAVIOR_SHA256,
        "universe": sorted(AGENT_UNIVERSE),
        "decision_session": f"every XNYS session from {FIRST_DECISION_SESSION}",
        "freeze_deadline": "before the open of the next session, checked against the clock immediately before the "
                           "record is written and against the database recording time in the same transaction; a "
                           "session not frozen in time is recorded FREEZE_MISSED (or FREEZE_LATE) and is never "
                           "reconstructed",
        "data_rule": "freeze when every universe stock and SPY has a bar at the session and every company's SEC "
                     f"filings were read; on a final attempt (within {FINAL_ATTEMPT_HOURS}h of the deadline) freeze "
                     "anyway: a stock without a bar has no decision, a stock whose filings could not be read has no "
                     "S3 decision; without a SPY bar the session is never frozen and becomes FREEZE_MISSED",
        "earnings_source": "data.sec.gov submissions (recent filings) for the company's current CIK: form 8-K with "
                           "item 2.02, acceptanceDateTime (UTC), reaction session = first close after acceptance "
                           "(events.earnings_announcements, the rule that built the replay dataset)",
        "s4_eligibility": "observed at the latest month-end session before the decision session "
                          "(event_replay._s4_eligibility)",
        "exit_levels": "the stop distance (signal-day ATR) and the S3 gap-fill level are frozen as ratios to the "
                       "signal close and rescaled to the scoring snapshot's close at the signal session",
        "trades": "entry at the next session's close; one open trade per stock and strategy; a trade is recorded "
                  "once its maximum-hold window has completed, from one price snapshot, and is final",
        "missing_bars": f"a trade whose window lacks a bar is retried for {RETRY_SESSIONS} sessions after the first "
                        "missing session, then EXCLUDED_MISSING_BARS and the stock is locked until its maximum hold "
                        "(the replay's rule)",
        "reward": er.PROTOCOL["reward"],
        "reward_version": fvf.REWARD_VERSION, "scoring_behavior_sha256": fvf.SCORING_BEHAVIOR_SHA256,
        "unit": "calendar month of the signal session: sum of that month's trade rewards divided by the number of "
                "universe stocks with a bar at the month's last frozen session; months without trades score 0",
        "evaluation": {
            "months": list(EVALUATION_MONTHS),
            "when": "once, after the last session of the final month and once every trade signalled in those months "
                    "is final; that record is final and later months are monitoring only",
            "interval": er.PROTOCOL["interval"], "family": er.PROTOCOL["family"],
            "coverage": f"at least {er.MIN_ACTIVE_MONTHS} active months and {er.MIN_TRADES} scored trades per "
                        "strategy, else INSUFFICIENT_COVERAGE",
            "verdict": "PASS_REQUIRES_INDEPENDENT_REVIEW when the monthly mean and its lower bound (family-wise for "
                       "S1-S3, unadjusted 95% for exploratory S4) are both positive, else NOT_SUPPORTED",
        },
        "timing_validity": "a decision created or recorded at or after its frozen deadline is "
                           "INVALID_PROSPECTIVE_TIMING and its signals are never traded",
        "qualification": "PASS_REQUIRES_INDEPENDENT_REVIEW; promotion NOT_AUTHORIZED; broker_submission false; the "
                         "agent sees the signals as information only",
    }


PROTOCOL = _protocol()
PROTOCOL_SHA256 = digest(PROTOCOL)
clean = fvf.clean


# ---- calendar ------------------------------------------------------------------

def decision_sessions_through(completed: str) -> list[str]:
    return session_dates(FIRST_DECISION_SESSION, completed) if completed >= FIRST_DECISION_SESSION else []


def deadline_for(session: str) -> datetime:
    return fvf.session_open(session_offset(session, 1))


def prior_month_end(session: str) -> str:
    """The latest month-end session strictly before the session (S4 eligibility date)."""
    candidate = fvf.month_end_session(session)
    if candidate >= session:
        candidate = fvf.month_end_session((date.fromisoformat(session[:7] + "-01") - timedelta(days=1)).isoformat())
    return candidate


def evaluation_end() -> str:
    year, month = map(int, EVALUATION_MONTHS[1].split("-"))
    return fvf.month_end_session(f"{year:04d}-{month:02d}-01")


def source_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    return hashlib.sha256(b"".join((root / p).read_bytes() for p in DECISION_SOURCES)).hexdigest()


# ---- SEC earnings announcements ------------------------------------------------

def sec_events(ciks: dict, session: str, *, client=None) -> tuple[dict, list[str]]:
    """ticker -> recent item 2.02 announcements accepted by the session's UTC date; failed tickers."""
    import httpx

    from ..config import SEC_USER_AGENT
    since = (date.fromisoformat(session) - timedelta(days=SEC_LOOKBACK_DAYS)).isoformat()
    own = client is None
    client = client or httpx.Client(timeout=30, headers={"User-Agent": SEC_USER_AGENT,
                                                         "Accept-Encoding": "gzip, deflate"})
    events, failed = {}, []
    try:
        for ticker, cik in sorted(ciks.items()):
            if not cik:
                failed.append(ticker)
                continue
            padded = f"{int(cik):010d}"
            for attempt in range(3):
                try:
                    response = client.get(f"https://data.sec.gov/submissions/CIK{padded}.json")
                    response.raise_for_status()
                    events[ticker] = ea.announcements(ticker, padded, ea.filings([response.json()]),
                                                      since=since, until=session)
                    break
                except Exception:
                    if attempt == 2:
                        failed.append(ticker)
                    else:
                        time.sleep(1.0 + attempt)
            time.sleep(0.12)
    finally:
        if own:
            client.close()
    return events, failed


# ---- decisions -----------------------------------------------------------------

def decide_session(session: str, *, prices: dict, spy: list[dict], events: dict, s4_eligible: set | None,
                   missing: list[str]) -> dict:
    """Flags for every stock with a bar at the session, from data through its close only.

    events: ticker -> announcements (a ticker absent from it has no S3 decision).
    Returns {"with_bar": [...], "missing": [...], "rows": [stocks with a flag or an announcement]}."""
    sessions = session_dates(session_offset(session, -HISTORY_SESSIONS), session)
    spy_bars = ss.Bars(spy, session, sessions)
    reaction_session = session_offset(session, -(ss.RULES["S3_post_earnings"]["reaction_sessions"] - 1))
    with_bar, absent, rows = [], set(missing), []
    for ticker in sorted(prices):
        if ticker in absent:
            continue
        data = prices[ticker]
        bars = ss.Bars(data, session, sessions)
        i = bars.index[session]
        if bars.close[i] is None:          # a stored but incomplete bar
            absent.add(ticker)
            continue
        with_bar.append(ticker)
        close = bars.close[i]
        flags = {"S1_volume_breakout": ss.s1_entry(bars, i), "S2_trend_pullback": ss.s2_entry(bars, i)}
        announcements = [e for e in events.get(ticker, []) if e["reaction_session"] == reaction_session]
        s3 = None
        if ticker not in events:
            flags["S3_post_earnings"] = None
        elif not announcements:
            flags["S3_post_earnings"] = False
        else:
            reaction = er.s3_reaction(bars, spy_bars, data, spy, reaction_session)
            passed = bool(reaction) and reaction["signal_index"] == i and \
                reaction["reaction_z"] >= ss.RULES["S3_post_earnings"]["z_min"]
            flags["S3_post_earnings"] = passed
            s3 = {"announcements": [{k: e[k] for k in ("accession", "acceptance_utc", "items", "reaction_session")}
                                    for e in announcements],
                  "reaction_z": reaction["reaction_z"] if reaction else None}
            if passed:
                s3.update(gap_ratio=reaction["gap_level"] / close, atr_ratio=reaction["atr_ref"] / close)
        flags["S4_value_momentum"] = (None if s4_eligible is None else
                                      ticker in s4_eligible and (flags["S1_volume_breakout"]
                                                                 or flags["S2_trend_pullback"]))
        if not any(flags.values()) and s3 is None:
            continue
        features = er._features(data, spy, session) if any(flags.values()) else None
        rows.append({"ticker": ticker, "close": close,
                     "atr_ratio": bars.atr[i] / close if bars.atr[i] is not None else None,
                     "flags": flags, "s3": s3,
                     "features": {k: features.get(k) for k in FEATURES_KEPT} if features else None})
    return {"with_bar": with_bar, "missing": sorted(absent), "rows": rows}


def decision_payload(session: str, decided: dict, *, protocol: dict, created_at: datetime, deadline: datetime,
                     sec_failed: list[str], s4_month_end: str, s4_eligible: set | None) -> dict:
    counts = {s: sum(bool(r["flags"].get(s)) for r in decided["rows"]) for s in STRATEGIES}
    return clean({
        "forward_id": FORWARD_ID, "protocol_record_id": protocol["record_id"],
        "protocol_content_hash": protocol["content_hash"], "decision_session": session,
        "entry_session": session_offset(session, 1), "freeze_deadline": deadline.isoformat(),
        "created_at": created_at.isoformat(),
        "code": {"git_sha": os.getenv("GITHUB_SHA"), "decision_sources_sha256": source_fingerprint(),
                 "strategy_behavior_sha256": er.STRATEGY_BEHAVIOR_SHA256},
        "with_bar": decided["with_bar"], "missing_bars_at_freeze": decided["missing"],
        "sec_unavailable": sorted(sec_failed), "s4_month_end": s4_month_end,
        "s4_eligible": sorted(s4_eligible) if s4_eligible is not None else None,
        "counts": counts, "rows": decided["rows"],
        "promotion": "NOT_AUTHORIZED", "broker_submission": False, "use": "INFORMATION_ONLY",
    })


# ---- trades --------------------------------------------------------------------

def outcome_key(strategy: str, ticker: str, signal: str) -> str:
    return f"{FORWARD_ID}:{strategy}:{ticker}:{signal}"


def signals_by_chain(decisions: list[dict]) -> dict:
    """(ticker, strategy) -> [(signal session, row, decision record)] oldest first, valid timing only."""
    chains = {}
    for d in sorted(decisions, key=lambda d: d["payload"]["decision_session"]):
        if not fvf.timing_valid(d):
            continue
        for row in d["payload"]["rows"]:
            for strategy, flag in row["flags"].items():
                if flag:
                    chains.setdefault((row["ticker"], strategy), []).append(
                        (d["payload"]["decision_session"], row, d))
    return chains


def _levels(row: dict, strategy: str, close_now: float) -> tuple[float | None, float | None]:
    if strategy == "S3_post_earnings":
        s3 = row.get("s3") or {}
        return s3.get("atr_ratio", 0.0) * close_now, s3.get("gap_ratio", 0.0) * close_now
    return (row["atr_ratio"] * close_now if row.get("atr_ratio") is not None else None), None


def resolve_chain(ticker: str, strategy: str, signals: list, *, bars: ss.Bars, stock: dict, spy: dict,
                  completed: str, recorded: dict) -> tuple[list, dict]:
    """New final trades for one stock and strategy, and the chain's state.

    recorded: outcome key -> payload already stored (final; their free_from is reused).
    State: CLEAR, OPEN (a trade whose window has not completed) or BLOCKED (bars missing)."""
    rule = ss.RULES[strategy]
    free_from, new = None, []
    for session, row, decision in signals:
        if free_from and session < free_from:
            continue
        key = outcome_key(strategy, ticker, session)
        if key in recorded:
            free_from = recorded[key]["free_from"]
            continue
        entry = session_offset(session, 1)
        last = session_offset(entry, rule["max_hold"])
        if completed < last:
            return new, {"state": "OPEN", "signal": session, "entry": entry, "window_ends": last}
        base = {"forward_id": FORWARD_ID, "strategy": strategy, "ticker": ticker, "signal": session,
                "entry": entry, "decision_record_id": decision["record_id"],
                "decision_content_hash": decision["content_hash"]}
        i = bars.index.get(session)
        close_now = bars.close[i] if i is not None else None
        exit_i, reason = ("EXCLUDED_MISSING_BARS", None) if close_now is None else ss.simulate_exit(
            bars, i + 1, strategy, **dict(zip(("atr_ref", "gap_level"), _levels(row, strategy, close_now))))
        path = None
        if isinstance(exit_i, int) and row.get("features") is not None:
            path = dr.rewards_between(entry, bars.sessions[exit_i], {"features": row["features"]}, stock=stock,
                                      spy=spy, horizon=rule["max_hold"])
        if exit_i == "EXCLUDED_MISSING_BARS" or (isinstance(exit_i, int) and row.get("features") is not None
                                                 and path is None):
            window = session_dates(session, last)
            first_missing = next((d for d in window if bars.close[bars.index[d]] is None or d not in spy),
                                 window[-1])
            if completed < session_offset(first_missing, RETRY_SESSIONS):
                return new, {"state": "BLOCKED", "signal": session, "first_missing": first_missing}
            outcome = {**base, "status": "EXCLUDED_MISSING_BARS", "first_missing": first_missing,
                       "free_from": session_offset(session, 1 + rule["max_hold"])}
        elif row.get("features") is None:
            outcome = {**base, "status": "NO_FEATURES", "exit": bars.sessions[exit_i], "reason": reason,
                       "free_from": bars.sessions[exit_i]}
        else:
            exit_session = bars.sessions[exit_i]
            outcome = {**base, "status": "SCORED", "exit": exit_session, "reason": reason,
                       "sessions_held": exit_i - (i + 1), "reward": path["rewards"]["LONG"], "beta": path["beta"],
                       "path_sha256": digest([stock.get(d) for d in session_dates(entry, exit_session)]),
                       "spy_path_sha256": digest([spy.get(d) for d in session_dates(entry, exit_session)]),
                       "free_from": exit_session, "reward_version": fvf.REWARD_VERSION}
        new.append((key, clean(outcome)))
        free_from = outcome["free_from"]
    return new, {"state": "CLEAR"}


# ---- evaluation ----------------------------------------------------------------

def denominators(decisions: list[dict]) -> dict:
    """month -> stocks with a bar at the month's last frozen (valid) session."""
    last = {}
    for d in decisions:
        if fvf.timing_valid(d):
            p = d["payload"]
            month = p["decision_session"][:7]
            if month not in last or p["decision_session"] > last[month]["decision_session"]:
                last[month] = p
    return {m: len(p["with_bar"]) for m, p in last.items()}


def evaluate(outcomes: list[dict], denominators_: dict, *, months: tuple[str, str] = EVALUATION_MONTHS) -> dict:
    lo, hi = months
    span = sorted(m for m in denominators_ if lo <= m <= hi)
    out = {}
    for strategy in STRATEGIES:
        trades = [o for o in outcomes if o.get("strategy") == strategy and lo <= o["signal"][:7] <= hi]
        scored = [t for t in trades if t["status"] == "SCORED"]
        confirm = strategy in er.CONFIRMATORY
        stat = er.interval(er.book(trades, denominators_, span), er.Z_FAMILY if confirm else None)
        active = len({t["signal"][:7] for t in scored})
        entry = {**stat, "trades": len(scored), "active_months": active,
                 "excluded": sum(t["status"] == "EXCLUDED_MISSING_BARS" for t in trades),
                 "no_features": sum(t["status"] == "NO_FEATURES" for t in trades),
                 "hit_rate": sum(t["reward"] > 0 for t in scored) / len(scored) if scored else None,
                 "role": "confirmatory" if confirm else "exploratory"}
        if active < er.MIN_ACTIVE_MONTHS or len(scored) < er.MIN_TRADES or stat.get("lower") is None:
            entry["verdict"] = "INSUFFICIENT_COVERAGE"
        else:
            entry["verdict"] = ("PASS_REQUIRES_INDEPENDENT_REVIEW" if stat["mean"] > 0 and stat["lower"] > 0
                                else "NOT_SUPPORTED")
        out[strategy] = entry
    return {"months": list(months), "months_with_decisions": len(span), "strategies": out,
            "promotion": "NOT_AUTHORIZED"}


# ---- database reads ------------------------------------------------------------

_COLS = fvf._COLS


def _rows(conn, sql, params):
    return [dict(zip(_COLS, r)) for r in conn.execute(sql, params).fetchall()]


def decisions(conn) -> list[dict]:
    return _rows(conn, """SELECT request_key,record_id,content_hash,payload,recorded_at::text
        FROM research_evidence_records WHERE kind=%s AND payload->>'forward_id'=%s
        ORDER BY payload->>'decision_session'""", (DECISION_KIND, FORWARD_ID))


def outcomes(conn) -> dict:
    return {r["request_key"]: r["payload"] for r in _rows(conn, """SELECT request_key,record_id,content_hash,
        payload,recorded_at::text FROM research_evidence_records WHERE kind=%s AND payload->>'forward_id'=%s
        ORDER BY request_key""", (OUTCOME_KIND, FORWARD_ID))}


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
    out = []
    for r in checks_with_status(conn, LOSS_STATUSES):
        known = r["payload"].get("deadline")
        recent = [t for t in (fvf._parse(known) if known else None, fvf._recorded_at(r))
                  if t is not None and timedelta(0) <= now - t <= timedelta(days=STICKY_DAYS)]
        if recent:
            out.append(f"{r['payload']['status']}:{r['payload']['decision_session']}")
    return out


# ---- database writes -----------------------------------------------------------

def _save_check(conn, key: str, payload: dict) -> bool:
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


def _register(conn) -> dict:
    from .. import research_store
    if er.PROTOCOL_SHA256 != REPLAY_PROTOCOL_SHA256:
        raise ProtocolChanged("REPLAY_PROTOCOL_CHANGED")
    try:
        return research_store.save(conn, PROTOCOL_KIND, FORWARD_ID, PROTOCOL)
    except ValueError as exc:
        raise ProtocolChanged("FORWARD_PROTOCOL_CHANGED") from exc


def _freeze(conn, read_conn, session: str, now: datetime, clock, protocol: dict, fetch_events) -> dict:
    from .. import db, research_store
    key = f"{FORWARD_ID}:{session}"
    existing = research_store.get(conn, DECISION_KIND, key)
    if existing:
        if not fvf.timing_valid(existing):
            if _record_loss(conn, session, fvf._deadline(existing["payload"]), "FREEZE_LATE"):
                return {"status": "FREEZE_LATE", "decision_session": session, "record_id": existing["record_id"]}
        conn.commit()
        return {"status": "REPLAYED", "decision_session": session, "record_id": existing["record_id"]}
    conn.commit()
    deadline = deadline_for(session)
    if now >= deadline:
        written = _record_loss(conn, session, deadline)
        return {"status": "FREEZE_MISSED" if written else "MISSED_ALREADY_RECORDED", "decision_session": session}
    universe = PROTOCOL["universe"]
    final_attempt = deadline - now <= timedelta(hours=FINAL_ATTEMPT_HOURS)
    read_conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    missing = fvf.bars_missing(read_conn, session, [*universe, "SPY"])
    if "SPY" in missing or (missing and not final_attempt):
        read_conn.rollback()
        return {"status": "FREEZE_PENDING_DATA", "decision_session": session, "missing": missing,
                "final_attempt": final_attempt}
    ciks = {t: (db.fetch_company(read_conn, t) or {}).get("cik") for t in universe}
    read_conn.rollback()
    events, sec_failed = fetch_events(ciks, session)
    if sec_failed and not final_attempt:
        return {"status": "FREEZE_PENDING_DATA", "decision_session": session, "sec_unavailable": sec_failed,
                "final_attempt": final_attempt}
    read_conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    prices = {t: db.fetch_prices(read_conn, t, session) for t in universe}
    spy = db.fetch_prices(read_conn, "SPY", session)
    month_end = prior_month_end(session)
    s4_eligible = er._s4_eligibility(read_conn, list(universe), [month_end])[month_end]
    read_conn.rollback()
    decided = decide_session(session, prices=prices, spy=spy, events=events, s4_eligible=s4_eligible,
                             missing=[t for t in missing if t != "SPY"])
    created = clock()
    if created >= deadline:   # the load ran past the open: never write a decision that is already late
        written = _record_loss(conn, session, deadline)
        return {"status": "FREEZE_MISSED" if written else "MISSED_ALREADY_RECORDED", "decision_session": session}
    payload = decision_payload(session, decided, protocol=protocol, created_at=created, deadline=deadline,
                               sec_failed=sec_failed, s4_month_end=month_end, s4_eligible=s4_eligible)
    saved = research_store.save(conn, DECISION_KIND, key, payload)
    late = fvf._recorded_at(saved) >= deadline
    if late:
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
            "missing_bars_at_freeze": payload["missing_bars_at_freeze"], "sec_unavailable": sorted(sec_failed),
            "decision_sources_changed": changed}


def freeze_due(conn, read_conn, completed: str, now: datetime, clock, protocol: dict, fetch_events) -> dict:
    """Record every earlier session that was never frozen, then freeze the latest if due."""
    sessions = decision_sessions_through(completed)
    if not sessions:
        return {"status": "NOTHING_DUE", "newly_missed": []}
    frozen = {d["payload"]["decision_session"] for d in decisions(conn)}
    recorded = {c["payload"]["decision_session"] for c in checks_with_status(conn, ("FREEZE_MISSED",))}
    newly_missed = []
    for session in sessions[:-1]:
        if session not in frozen and session not in recorded and _record_loss(conn, session, deadline_for(session)):
            newly_missed.append(session)
    latest = sessions[-1]
    if latest not in frozen and latest in recorded:
        return {"status": "MISSED_ALREADY_RECORDED", "decision_session": latest, "newly_missed": newly_missed}
    return {**_freeze(conn, read_conn, latest, now, clock, protocol, fetch_events), "newly_missed": newly_missed}


def score_pending(conn, read_conn, completed: str) -> dict:
    """Record every trade whose window has completed; one stock's error never stops the others."""
    from .. import db, research_store
    stats = {"scored": 0, "excluded": 0, "no_features": 0, "open": 0, "blocked": [], "invalid": [], "errors": [],
             "unresolved_from": None}
    frozen = decisions(conn)
    for d in frozen:
        if not fvf.timing_valid(d) and _save_check(conn, f"{d['request_key']}:INVALID_PROSPECTIVE_TIMING", {
                "decision_session": d["payload"]["decision_session"], "status": "INVALID_PROSPECTIVE_TIMING",
                "deadline": d["payload"]["freeze_deadline"], "decision_record_id": d["record_id"]}):
            stats["invalid"].append(d["payload"]["decision_session"])
    conn.commit()
    chains = signals_by_chain(frozen)
    tickers = sorted({t for t, _ in chains})
    read_conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    prices = {t: db.fetch_prices(read_conn, t, completed) for t in tickers}
    spy_rows = db.fetch_prices(read_conn, "SPY", completed)
    read_conn.rollback()
    recorded = outcomes(conn)
    spy = dr._adjusted(spy_rows)
    first = min((s for chain in chains.values() for s, _, _ in chain), default=completed)
    sessions = session_dates(session_offset(first, -HISTORY_SESSIONS), completed)
    unresolved = []
    for (ticker, strategy), signals in sorted(chains.items()):
        try:
            bars = ss.Bars(prices[ticker], completed, sessions)
            new, state = resolve_chain(ticker, strategy, signals, bars=bars, stock=dr._adjusted(prices[ticker]),
                                       spy=spy, completed=completed, recorded=recorded)
            for key, outcome in new:
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("strategy-forward:" + key,))
                if not research_store.get(conn, OUTCOME_KIND, key):
                    research_store.save(conn, OUTCOME_KIND, key, outcome, ticker=ticker)
                    stats[{"SCORED": "scored", "EXCLUDED_MISSING_BARS": "excluded",
                           "NO_FEATURES": "no_features"}[outcome["status"]]] += 1
                conn.commit()
            if state["state"] == "OPEN":
                stats["open"] += 1
            elif state["state"] == "BLOCKED":
                stats["blocked"].append(f"{strategy}:{ticker}:{state['signal']}")
            if state["state"] != "CLEAR":
                unresolved.append(state["signal"])
        except Exception as exc:
            for c in (conn, read_conn):
                with suppress(Exception):
                    c.rollback()
            stats["errors"].append({"chain": f"{strategy}:{ticker}", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            unresolved.append(signals[0][0])
    stats["unresolved_from"] = min(unresolved) if unresolved else None
    return stats


def _ready_for_final(completed: str, stats: dict) -> bool:
    end = evaluation_end()
    return (completed > end and not stats["errors"]
            and (stats["unresolved_from"] is None or stats["unresolved_from"] > end))


def run_daily(*, now: datetime | None = None, clock=None, fetch_events=None) -> dict:
    """Freeze the latest session if due, then record completed trades, then evaluate once."""
    from .. import db, research_store
    from ..admin_panel.store import audit

    if clock is None:
        clock = (lambda: now) if now is not None else _utcnow
    now = now or clock()
    if now.tzinfo is None:
        raise ValueError("FORWARD_TIMESTAMP_NOT_AWARE")
    completed = latest_completed_session(now)
    receipt = {"stage": "strategy_forward", "forward_id": FORWARD_ID, "completed_session": completed,
               "promotion": "NOT_AUTHORIZED", "broker_submission": False}
    if os.getenv("GITHUB_REF") != "refs/heads/main":
        return {**receipt, "status": "REFUSED_NON_MAIN", "reason_code": os.getenv("GITHUB_REF") or "UNSET"}
    try:
        return _run_locked(receipt, completed, now, clock, fetch_events or sec_events, db, research_store, audit)
    except Exception as exc:
        return {**receipt, "status": "FAILED", "reason_code": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _run_locked(receipt, completed, now, clock, fetch_events, db, research_store, audit) -> dict:
    from . import reward
    with db.connect() as lock:
        lock.autocommit = True
        if not lock.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (FORWARD_ID,)).fetchone()[0]:
            return {**receipt, "status": "BUSY"}
        try:
            audit(lock, ACTOR, "STRATEGY_FORWARD_STAGE_STARTED", {**receipt, "status": "STARTED"})
            with db.connect() as conn, db.connect() as read_conn:
                protocol = _register(conn)
                conn.commit()
                receipt.update(protocol_sha256=PROTOCOL_SHA256, protocol_record_id=protocol["record_id"])
                if ss.behavior_digest() != er.STRATEGY_BEHAVIOR_SHA256:
                    raise ProtocolChanged("STRATEGY_BEHAVIOR_CHANGED")
                freeze = freeze_due(conn, read_conn, completed, now, clock, protocol, fetch_events)
                receipt["freeze"] = freeze
                try:
                    if reward.VERSION != fvf.REWARD_VERSION or fvf.scoring_digest() != fvf.SCORING_BEHAVIOR_SHA256:
                        raise ProtocolChanged("SCORING_BEHAVIOR_CHANGED")  # never blocks the freeze above
                    stats = score_pending(conn, read_conn, completed)
                except Exception as exc:
                    for c in (conn, read_conn):
                        with suppress(Exception):
                            c.rollback()
                    stats = {"scored": 0, "blocked": [], "invalid": [], "unresolved_from": None,
                             "errors": [{"error": f"{type(exc).__name__}: {str(exc)[:200]}"}]}
                receipt["outcomes"] = stats
                final = final_evaluation(conn)
                if not final and _ready_for_final(completed, stats):
                    research_store.save(conn, EVALUATION_KIND, f"{FORWARD_ID}:final", clean({
                        "forward_id": FORWARD_ID, "evaluated_through": completed,
                        **evaluate(list(outcomes(conn).values()), denominators(decisions(conn)))}))
                    conn.commit()
                    final = final_evaluation(conn)
                sticky = sticky_attention(conn, clock())
            attention = (freeze["status"] not in OK_FREEZE or freeze.get("newly_missed")
                         or freeze.get("decision_sources_changed") or freeze.get("sec_unavailable")
                         or stats["invalid"] or stats["errors"] or stats["blocked"] or sticky)
            receipt.update(status="ATTENTION" if attention else "OK", sticky_attention=sticky,
                           evaluation_final=bool(final))
        except ProtocolChanged as exc:
            receipt.update(status="PROTOCOL_CHANGED", reason_code=str(exc))
        except Exception as exc:
            receipt.update(status="FAILED", reason_code=f"{type(exc).__name__}: {str(exc)[:200]}")
        finally:
            try:
                audit(lock, ACTOR, "STRATEGY_FORWARD_STAGE_FINISHED",
                      clean({**receipt, "status": receipt.get("status", "FAILED")}))
            except Exception:
                receipt["audit_failed"] = True
                if receipt.get("status") == "OK":
                    receipt["status"] = "ATTENTION"
            with suppress(Exception):
                lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (FORWARD_ID,))
        return receipt


# ---- read-only views -----------------------------------------------------------

def open_trades(frozen: list[dict], recorded: dict) -> list[dict]:
    """The one unresolved trade per stock and strategy (taken, window not yet recorded)."""
    out = []
    for (ticker, strategy), signals in sorted(signals_by_chain(frozen).items()):
        free_from = None
        for session, _, _ in signals:
            if free_from and session < free_from:
                continue
            key = outcome_key(strategy, ticker, session)
            if key in recorded:
                free_from = recorded[key]["free_from"]
                continue
            entry = session_offset(session, 1)
            out.append({"ticker": ticker, "strategy": strategy, "signal": session, "entry": entry,
                        "window_ends": session_offset(entry, ss.RULES[strategy]["max_hold"])})
            break
    return out


def summary(conn, *, now: datetime | None = None, ticker: str | None = None) -> dict:
    """Read-only status for the API, the agent and the watchdog (one stock's view with ticker)."""
    now = now or _utcnow()
    conn.execute("SET TRANSACTION READ ONLY")
    completed = latest_completed_session(now)
    protocol = conn.execute("SELECT record_id,content_hash FROM research_evidence_records WHERE kind=%s AND "
                            "request_key=%s", (PROTOCOL_KIND, FORWARD_ID)).fetchone()
    frozen = decisions(conn)
    recorded = outcomes(conn)
    sessions = decision_sessions_through(completed)
    frozen_sessions = {d["payload"]["decision_session"] for d in frozen}
    expected = sessions[-1] if sessions else None
    deadline = deadline_for(expected) if expected else None
    missing = bool(expected) and expected not in frozen_sessions
    missed = sorted({c["payload"]["decision_session"] for c in checks_with_status(conn, ("FREEZE_MISSED", "FREEZE_LATE"))}
                    | {s for s in sessions[:-1] if s not in frozen_sessions})
    latest = frozen[-1]["payload"] if frozen else None
    trades = list(recorded.values())
    opened = open_trades(frozen, recorded)
    if ticker:
        trades = [t for t in trades if t["ticker"] == ticker]
        opened = [t for t in opened if t["ticker"] == ticker]
    latest_rows = [r for r in (latest or {}).get("rows", []) if not ticker or r["ticker"] == ticker]
    final = final_evaluation(conn)
    monitoring = evaluate(list(recorded.values()), denominators(frozen),
                          months=(FIRST_DECISION_SESSION[:7], completed[:7]))
    for entry in monitoring["strategies"].values():
        entry.pop("verdict")              # no verdict before the registered evaluation
    return clean({
        "status": "OK", "forward_id": FORWARD_ID, "protocol_registered": bool(protocol),
        "protocol_sha256": PROTOCOL_SHA256, "confirms_replay_protocol_sha256": REPLAY_PROTOCOL_SHA256,
        "first_decision_session": FIRST_DECISION_SESSION, "evaluation_months": list(EVALUATION_MONTHS),
        "completed_session": completed, "expected_decision_session": expected,
        "freeze_missing": missing and deadline is not None and now >= deadline,
        "freeze_window_open": missing and deadline is not None and now < deadline,
        "frozen_sessions": len(frozen_sessions), "missed_sessions": missed[-20:], "missed_count": len(missed),
        "latest_decision": {"decision_session": latest["decision_session"], "counts": latest["counts"],
                            "with_bar": (ticker in latest["with_bar"]) if ticker else len(latest["with_bar"]),
                            "missing_bars": latest["missing_bars_at_freeze"],
                            "sec_unavailable": latest["sec_unavailable"], "rows": latest_rows} if latest else None,
        "open_trades": opened,
        "recent_trades": sorted(trades, key=lambda t: (t.get("exit") or t["signal"], t["strategy"]))[-25:],
        "monitoring": {**monitoring, "label": "MONITORING_NOT_A_VERDICT"} if not final else None,
        "evaluation": {**final, "final": True} if final else {"status": "PENDING", "final": False},
        "sticky_attention": sticky_attention(conn, now),
        "use": "INFORMATION_ONLY", "promotion": "NOT_AUTHORIZED", "broker_submission": False,
    })
