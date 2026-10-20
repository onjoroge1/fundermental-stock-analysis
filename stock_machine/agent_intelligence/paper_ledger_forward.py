"""Pre-registered evaluation of the agent's paper ledger (the RESEARCH/PAPER switch's ledger).

PAPER mode (set 2026-10-07) simulates the agent's gated stock trades in the Agent Trading v1
ledger (agent_trading.py): entry at the first close after the decision is recorded, a fixed
20-session hold, 10 bps a side. Its P&L is not evidence of anything until it is judged by a
rule fixed in advance; this module is that rule, registered before the first learning labels
mature (2026-11-10), so before the confidence-gated bandit can open a position.

- The policy under test is whatever PAPER executes in the window (intention to treat): a later
  version, a direction-policy switch or a pause never removes a position; a change is recorded.
- The ledger tables can be updated, so every in-scope position is copied append-only the night it
  is first seen (entry) and the night it is first seen closed (exit); a ledger row that later
  differs is LEDGER_MUTATED. A copy made late, or a night without a copy, is recorded.
- Each position is scored by the append-only counterfactual label of its own decision and side
  (AGENT_REWARD_V3), never by the mutable ledger: reward v5, the residual against beta x SPY net of
  20 bps over ex-ante volatility. Abstaining scores 0: "do the paper trades beat not trading".
  The ledger is reconciled against the label and the result is recorded, not used to drop it.
- One verdict, once, on positions entered in EVALUATION_MONTHS (extended once, by counts only, to
  EXTENDED_MONTHS), from monthly book values (sum of scores / 54, empty months 0), Newey-West
  lag 1 with a t(n-1) 95% interval. A pass must survive scoring every excluded position at the
  worst observed score. It is underpowered for realistic edges and says so.

Records reuse the FV_FORWARD_* evidence kinds (migration 0028) under this module's FORWARD_ID and
every query filters on it. Read-only toward the ledger: no write to an agent_* table, no mode
change, no sizing, no broker path. Information only; no module that decides, learns or trades
imports it.
"""
from __future__ import annotations

import hashlib
import math
import os
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from ..agents.contracts import digest
from ..market_calendar import latest_completed_session, session_dates, session_offset
from . import fair_value_forward as fvf
from . import inference

FORWARD_ID = "agent-paper-ledger.v1:evaluation.v1"
EVALUATION_MONTHS = ("2026-11", "2028-10")
EXTENDED_MONTHS = ("2026-11", "2029-10")
# Registered before this session opens, or the evaluation is REGISTERED_LATE (no verdict): the
# first prospective labels mature that day, so no gated position can exist before it.
REGISTER_BY_SESSION = "2026-11-10"
MIN_POSITIONS = 60
MIN_ACTIVE_MONTHS = 12
BOOK_DENOMINATOR = 54     # the registered sizing: each position is 0.50 / 54 of equity
NW_LAG = 1                # ceil(20 / 21): a 20-session hold spans at most two calendar months
RETRY_SESSIONS = 10
MODE_MIN_SHARE = 0.9
MAX_EXCEPTION_SHARE = 0.1
EXCLUSION_FLOOR = -3.0    # an excluded position counts as at least this bad when a pass is checked
STICKY_DAYS = 7
# The paper stage runs 17:00-22:59 ET (scheduled_operations): a session counts only if PAPER is the
# mode and research capture is not paused for the whole of it.
PAPER_WINDOW = (timedelta(hours=7, minutes=30), timedelta(hours=13, minutes=30))   # from the 09:30 open
LEGACY_SELECTORS = ("source-report-classification", "fundamental-score-paper-v1")
# The policy and ledger as registered (literals): a later difference is POLICY_VERSION_CHANGED,
# recorded once and never a reason to drop a position or a new protocol.
POLICY = {
    "selector_version": "agent-intelligence.v2",
    "execution_contract": "prospective-next-close.v2",
    "bandit_version": "pooled-linear-confident-sector.v3", "confidence_z": 0.8416,
    "reward_version": "risk-scaled-residual-paper-reward.v5",
    "learning_basis": "PROSPECTIVE_COUNTERFACTUAL_V1", "learning_contract": "prospective-counterfactual.v1",
    "holding_sessions": 20, "fill_cost_bps": 10.0, "starting_equity_usd": 100_000.0,
    "max_gross_pct": 0.50, "target_position_pct": 0.50 / BOOK_DENOMINATOR,
}
EVALUATION_BEHAVIOR_SHA256 = "c8336b1c998eadb31017ad3c840caebccfb68f0a69c6c34df68d4e98141e232d"

PROTOCOL_KIND = fvf.PROTOCOL_KIND
DECISION_KIND = fvf.DECISION_KIND
OUTCOME_KIND = fvf.OUTCOME_KIND
CHECK_KIND = fvf.CHECK_KIND
EVALUATION_KIND = fvf.EVALUATION_KIND
ACTOR = "paper-ledger-forward-scheduler"
LABEL_KIND = "AGENT_REWARD_V3"
SIDES = {"LONG": "LONG_STOCK", "SHORT": "SHORT_STOCK"}
SCORED = ("SCORED",)
EXCLUDED = ("EXCLUDED_MISSING_LABEL", "ENTRY_MISMATCH", "EXCLUDED_LABEL_INCOMPLETE")
OUT_OF_SCOPE = ("PRE_REGISTRATION", "OTHER_SELECTOR", "LEGACY_CONTRACT", "OUTSIDE_WINDOW")
# Listed for STICKY_DAYS (and counted for good in recorded_checks and the final record).
LOSS_STATUSES = ("LEDGER_MUTATED", "SNAPSHOT_LATE", "NIGHT_MISSED", "POLICY_VERSION_CHANGED",
                 "SCOPE_EXCLUDED_AFTER_REGISTRATION")
RECORDED_STATUSES = (*LOSS_STATUSES, "DECISION_SOURCES_CHANGED", "EXTENDED_TO_36_MONTHS")
SCOPE_FIELDS = ("selector_version", "execution_contract", "has_commitment", "decision_recorded_at")
ENTRY_FIELDS = ("ticker", "side", "source_decision_id", "source_intent_id", "entry_market_date", "entry_price",
                "paper_units", "entry_notional_usd", "entry_cost_usd")
EXIT_FIELDS = ("status", "exit_market_date", "exit_price", "exit_cost_usd", "realized_pnl_usd", "exit_reason")
# Code that produces the ledger and its labels (provenance; a change is recorded on its night).
DECISION_SOURCES = (
    "agent_intelligence/paper_ledger_forward.py", "agent_trading.py", "agent_cycle.py", "scheduled_operations.py",
    "agent_intelligence/orchestrator.py", "agent_intelligence/bandit.py", "agent_intelligence/state.py",
    "agent_intelligence/strategy_router.py", "agent_intelligence/instructions.py", "agent_intelligence/learning.py",
    "agent_intelligence/outcomes.py", "agent_intelligence/reward.py", "agent_intelligence/reconcile.py",
    "agent_intelligence/eligibility.py", "agent_intelligence/inference.py", "market_calendar.py",
)


class ProtocolChanged(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tolerance() -> float:
    from .reconcile import TOLERANCE_PCT
    return TOLERANCE_PCT


def _protocol() -> dict:
    return {
        "forward_id": FORWARD_ID,
        "ledger": "Agent Trading v1 (stock_machine/agent_trading.py: agent_trade_intents, agent_paper_positions); "
                  "not the legacy paper.py book (/api/paper) and not forward_paper_v2",
        "policy_as_registered": POLICY,
        "register_by": f"before the open of {REGISTER_BY_SESSION}, else REGISTERED_LATE and no verdict",
        "scope": "every ledger position whose intent records the execution_contract and the holding_commitment field "
                 "(the committed 20-session hold code) and a selector other than a legacy one "
                 f"({', '.join(LEGACY_SELECTORS)}), from a decision recorded after this protocol, entered in the "
                 "evaluation months; others are listed as PRE_REGISTRATION, OTHER_SELECTOR (legacy or no selector), "
                 "LEGACY_CONTRACT or OUTSIDE_WINDOW and never scored; a post-registration position entered by the end "
                 "of the extended months but excluded by selector or contract is also recorded as "
                 "SCOPE_EXCLUDED_AFTER_REGISTRATION",
        "intention_to_treat": "a version change, a direction-policy switch, a mode toggle or a capture pause never "
                              "removes a position; a selector, contract or registered policy value that differs is "
                              "recorded once as POLICY_VERSION_CHANGED",
        "snapshots": "each in-scope position is copied append-only the night it is first seen and the night it is "
                     "first seen closed, and stays in scope once copied; a ledger row that later differs in any "
                     "copied field (or disappears) is LEDGER_MUTATED; a first copy made more than one session after "
                     "entry is SNAPSHOT_LATE; a session from the window's first session to the end of the extended "
                     "months, after registration, without a nightly copy is NIGHT_MISSED",
        "score": "the decision's AGENT_REWARD_V3 label for the position's side: (residual_return_pct - costs_pct) / "
                 "risk_scale_pct, reward v5 (residual against frozen beta x SPY over the committed window, net of "
                 "20 bps, over ex-ante 20-session volatility); abstaining scores 0",
        "scoring": f"a position is scored from its label once the label exists and either its exit is copied or "
                   f"{RETRY_SESSIONS} sessions have passed since its due session (UNSETTLED); the ledger is reconciled "
                   f"against the label (reconcile.classify, tolerance {repr(_tolerance())} pp: MATCHED, LATE_EXIT, EARLY_EXIT, "
                   "RETURN_MISMATCH, INVALID_INPUTS) and the result recorded, never used to drop it; excluded and "
                   f"listed: no label {RETRY_SESSIONS} sessions after the due session (EXCLUDED_MISSING_LABEL), a label "
                   "whose entry session differs from the position's (ENTRY_MISMATCH), a label without a finite "
                   "residual, cost or positive risk scale (EXCLUDED_LABEL_INCOMPLETE)",
        "unit": f"calendar month of the entry session: sum of that month's position scores divided by "
                f"{BOOK_DENOMINATOR} (the registered sizing); months without positions score 0",
        "statistic": f"mean of the monthly values over every month of the window, Newey-West lag {NW_LAG}, "
                     "t(n-1) two-sided 95% interval (inference.py)",
        "evaluation": {
            "months": list(EVALUATION_MONTHS), "extended_months": list(EXTENDED_MONTHS),
            "when": "once, after the window's last session and once every in-scope position entered in it is "
                    "scored or excluded; that record is final",
            "minimum": f"at least {MIN_POSITIONS} scored positions and {MIN_ACTIVE_MONTHS} months with one; if the "
                       "window ends short, it is extended once to the extended months (decided on these counts "
                       "alone, no estimate computed); short again is INSUFFICIENT_SAMPLE",
            "mode_rule": f"a share of at least {repr(MODE_MIN_SHARE)} of the window's sessions with PAPER the mode and "
                         "research capture not paused for the whole paper stage (17:00-23:00 ET), from the "
                         "TRADING_MODE_CHANGED and CAPTURE_PAUSE_CHANGED audit trails, else MODE_INTERRUPTED and no "
                         "verdict",
            "data_rule": f"excluded positions plus scored positions first copied late, as a share of in-scope "
                         f"positions, above {repr(MAX_EXCEPTION_SHARE)} is DATA_COMPROMISED and no verdict",
            "verdict": "PASS_REQUIRES_INDEPENDENT_REVIEW when the mean and its lower bound are both above 0 and the "
                       "lower bound stays above 0 with every excluded position scored at the lower of the lowest "
                       f"observed score and {repr(EXCLUSION_FLOOR)}; "
                       "NEGATIVE when the upper bound is below 0 (worse than not trading); else NOT_SUPPORTED; every "
                       "EVALUATED result reports the 80%-power detectable difference",
            "interim": "before the final record only coverage is reported (counts, months, mode share, exclusions "
                       "and recorded checks); no mean, interval or 'on track' signal",
        },
        "power": "underpowered for realistic edges: for independent positions with reward sd 1 the 80%-power "
                 "detectable mean is about 3.0 / sqrt(N) per position (0.39 at the 60-position minimum, 0.20 at "
                 "225, 0.10 at 850, about the 20-session capacity of 35 stocks over 24 months); same-month "
                 "correlation (about 0.05 in the 2016-2026 direction replay) raises it about 1.2x at 10 positions "
                 "a month and 1.6x at 35; the false-pass rate is about 3-4% against a nominal 2.5%; earlier tests "
                 "put edges near 0.00-0.04, so NOT_SUPPORTED (or INSUFFICIENT_SAMPLE if the gate rarely trades) is "
                 "the likely verdict and is not evidence of no edge",
        "not_evidence": "ledger P&L, open marks, the weekly account-vs-SPY audit and anything before the final record",
        "qualification": "PASS_REQUIRES_INDEPENDENT_REVIEW; not independent of the direction test (its positions "
                         "are a subset of the same labels); promotion NOT_AUTHORIZED; broker_submission false; "
                         "never changes the mode, sizing, gate, bandit or eligibility",
        "evaluation_behavior": "this module's scope, score, mode and verdict code must reproduce "
                               "evaluation_behavior_sha256 on a fixed synthetic ledger or nothing is scored or "
                               "evaluated (snapshots continue)",
        "evaluation_behavior_sha256": EVALUATION_BEHAVIOR_SHA256,
    }


PROTOCOL = _protocol()
PROTOCOL_SHA256 = digest(PROTOCOL)
clean = fvf.clean


# ---- calendar ------------------------------------------------------------------

def month_end(month: str) -> str:
    return fvf.month_end_session(f"{month}-01")


def _previous_month(month: str) -> str:
    year, m = map(int, month.split("-"))
    return f"{year - 1:04d}-12" if m == 1 else f"{year:04d}-{m - 1:02d}"


def window_sessions(months: tuple[str, str], through: str | None = None) -> list[str]:
    end = month_end(months[1])
    if through and through < end:
        end = through
    start = session_offset(month_end(_previous_month(months[0])), 1)
    return session_dates(start, end) if end >= start else []


def months_between(first: str, last: str) -> list[str]:
    out, year, m = [], *map(int, first.split("-"))
    while f"{year:04d}-{m:02d}" <= last:
        out.append(f"{year:04d}-{m:02d}")
        year, m = (year + 1, 1) if m == 12 else (year, m + 1)
    return out


def due_session(entry: str) -> str:
    return session_offset(entry, POLICY["holding_sessions"])


# ---- pure rules (pinned by the behaviour digest) --------------------------------

def scope(position: dict, registered_at: datetime | None, months: tuple[str, str]) -> str | None:
    """None when the position is in scope; else the reason it is not."""
    if position.get("selector_version") in LEGACY_SELECTORS or not position.get("selector_version"):
        return "OTHER_SELECTOR"
    if not position.get("execution_contract") or not position.get("has_commitment"):
        return "LEGACY_CONTRACT"
    recorded = position.get("decision_recorded_at")
    if registered_at is None or not recorded or fvf._parse(recorded) <= registered_at:
        return "PRE_REGISTRATION"
    if not months[0] <= str(position["entry_market_date"])[:7] <= months[1]:
        return "OUTSIDE_WINDOW"
    return None


def score(entry: dict, exit_: dict | None, label: dict | None, completed: str) -> dict | None:
    """The position's outcome from its label (and its copies), or None while it can still resolve."""
    from .reconcile import classify
    due = due_session(entry["entry_market_date"])
    deadline = completed >= session_offset(due, RETRY_SESSIONS)
    base = {"position_id": entry["position_id"], "ticker": entry["ticker"], "side": entry["side"],
            "decision_id": entry["source_decision_id"], "entry_session": entry["entry_market_date"],
            "entry_month": entry["entry_market_date"][:7], "due_session": due,
            "snapshot_late": bool(entry.get("snapshot_late"))}
    if not label:
        return {**base, "status": "EXCLUDED_MISSING_LABEL",
                "missing": ["label", *([] if exit_ else ["exit"])]} if deadline else None
    if not exit_ and not deadline:
        return None                                           # wait for the ledger to settle, then reconcile
    arm = ((label.get("arms") or {}).get(SIDES.get(entry["side"], "")) or {})
    outcome, reward = arm.get("outcome") or {}, arm.get("reward") or {}
    if outcome.get("entry_date") != entry["entry_market_date"]:
        return {**base, "status": "ENTRY_MISMATCH", "label_entry": outcome.get("entry_date")}
    try:
        residual = float(outcome["residual_return_pct"])
        costs = float(outcome["costs_pct"])
        scale = float((reward.get("risk_scale") or {}).get("risk_scale_pct")
                      or (reward.get("components") or {}).get("risk_scale_pct"))
    except (KeyError, TypeError, ValueError):
        return {**base, "status": "EXCLUDED_LABEL_INCOMPLETE"}
    if not all(math.isfinite(v) for v in (residual, costs, scale)) or scale <= 0:
        return {**base, "status": "EXCLUDED_LABEL_INCOMPLETE"}
    if exit_:
        row = classify({**entry, **exit_, "position_id": entry["position_id"]}, label)
        recon = row["status"]
        if recon == "LATE_EXIT" and str(exit_.get("exit_market_date")) < str(outcome.get("exit_date")):
            recon = "EARLY_EXIT"
    else:
        row, recon = None, "UNSETTLED"
    return {**base, "status": "SCORED", "score": round((residual - costs) / scale, 6), "reconciliation": recon,
            "reconciliation_detail": row, "realized_pnl_usd": (exit_ or {}).get("realized_pnl_usd"),
            "label_reward_version": reward.get("schema_version"), "residual_return_pct": residual,
            "gross_return_pct": outcome.get("gross_return_pct"), "market_return_pct": outcome.get("market_return_pct"),
            "beta": outcome.get("beta"), "costs_pct": costs, "risk_scale_pct": scale}


def mode_share(mode_events: list[dict], pause_events: list[dict], sessions: list[str], current_mode: str | None,
               current_paused: bool | None) -> float | None:
    """Share of sessions whose whole paper stage (17:00-23:00 ET) ran with PAPER on and capture unpaused.

    Before the first event of a kind, that event's prior value holds; with no events, the current value."""
    if not sessions:
        return None
    trail = sorted([(fvf._parse(e["occurred_at"]), "mode", e.get("after")) for e in mode_events]
                   + [(fvf._parse(e["occurred_at"]), "paused", bool(e.get("after"))) for e in pause_events],
                   key=lambda x: x[0])
    first_mode = min(mode_events, key=lambda e: e["occurred_at"]) if mode_events else None
    first_pause = min(pause_events, key=lambda e: e["occurred_at"]) if pause_events else None
    state = {"mode": first_mode.get("before") if first_mode else current_mode,
             "paused": bool(first_pause.get("before")) if first_pause else bool(current_paused)}
    good, i = 0, 0
    for s in sessions:
        opened = fvf.session_open(s)
        start, end = opened + PAPER_WINDOW[0], opened + PAPER_WINDOW[1]
        while i < len(trail) and trail[i][0] <= start:
            state[trail[i][1]] = trail[i][2]
            i += 1
        ok = state["mode"] == "PAPER" and not state["paused"]
        while i < len(trail) and trail[i][0] <= end:
            state[trail[i][1]] = trail[i][2]
            ok = ok and state["mode"] == "PAPER" and not state["paused"]
            i += 1
        good += ok
    return good / len(sessions)


def _book(scores: list[tuple[str, float]], labels: list[str]) -> list[float]:
    book = {m: 0.0 for m in labels}
    for month, value in scores:
        book[month] += value / BOOK_DENOMINATOR
    return [book[m] for m in labels]


def _interval(values: list[float]) -> dict:
    n = len(values)
    mean = sum(values) / n
    se = inference.newey_west_se(values, NW_LAG)
    t = inference.t_quantile_975(n - 1)
    return {"mean": mean, "se": se, "lower": mean - t * se, "upper": mean + t * se,
            "detectable": inference.detectable_difference(se, n)}


def evaluate(outcomes: list[dict], months: tuple[str, str], *, share: float | None) -> dict:
    """The verdict on the window's outcomes (or the reason there is none)."""
    in_window = [o for o in outcomes if months[0] <= o["entry_month"] <= months[1]]
    scored = [o for o in in_window if o["status"] in SCORED and o.get("score") is not None]
    excluded = [o for o in in_window if o["status"] in EXCLUDED]
    late = [o for o in scored if o.get("snapshot_late")]
    labels = months_between(*months)
    active = sorted({o["entry_month"] for o in scored})
    exceptions = (len(excluded) + len(late)) / ((len(scored) + len(excluded)) or 1)
    counts = {"months": len(labels), "active_months": len(active), "scored_positions": len(scored),
              "excluded_positions": len(excluded), "late_snapshots_scored": len(late),
              "exception_share": round(exceptions, 6),
              "excluded_by_status": {s: sum(o["status"] == s for o in excluded) for s in EXCLUDED},
              "reconciliation": {r: sum(o.get("reconciliation") == r for o in scored)
                                 for r in sorted({o.get("reconciliation") for o in scored} - {None})},
              "paper_mode_share": None if share is None else round(share, 6)}
    if share is not None and share < MODE_MIN_SHARE:
        return {**counts, "status": "MODE_INTERRUPTED", "verdict": None}
    if exceptions > MAX_EXCEPTION_SHARE:
        return {**counts, "status": "DATA_COMPROMISED", "verdict": None}
    if len(scored) < MIN_POSITIONS or len(active) < MIN_ACTIVE_MONTHS or len(labels) < 2:
        return {**counts, "status": "INSUFFICIENT_SAMPLE", "verdict": None}
    values = _book([(o["entry_month"], o["score"]) for o in scored], labels)
    main = _interval(values)
    worst = min(EXCLUSION_FLOOR, min(o["score"] for o in scored))
    imputed = _interval(_book([(o["entry_month"], o["score"]) for o in scored]
                              + [(o["entry_month"], worst) for o in excluded], labels))
    robust = imputed["lower"] > 0
    verdict = ("PASS_REQUIRES_INDEPENDENT_REVIEW" if main["mean"] > 0 and main["lower"] > 0 and robust else
               "NEGATIVE" if main["upper"] < 0 else "NOT_SUPPORTED")
    per_position = BOOK_DENOMINATOR * len(values) / len(scored)
    return {**counts, "status": "EVALUATED", "verdict": verdict, "monthly_mean": main["mean"],
            "newey_west_se": main["se"], "lower": main["lower"], "upper": main["upper"],
            "detectable_difference_80pct": main["detectable"],
            "exclusion_sensitivity": {"imputed_score": worst, "imputed_positions": len(excluded),
                                      "lower": imputed["lower"], "pass_survives": robust},
            "per_position": {k: main[v] * per_position for k, v in (("mean", "mean"), ("lower", "lower"),
                                                                     ("upper", "upper"),
                                                                     ("detectable_difference_80pct", "detectable"))},
            "monthly_values": {m: round(v, 8) for m, v in zip(labels, values)}}


def decide(outcomes: list[dict], *, extended: bool, share: float | None, registered_late: bool,
           months: tuple[str, str] = EVALUATION_MONTHS, extended_months: tuple[str, str] = EXTENDED_MONTHS) -> dict:
    """Final record content for the current window (extension is decided on counts alone)."""
    window = extended_months if extended else months
    if registered_late:
        return {"status": "REGISTERED_LATE", "verdict": None, "window": list(window), "extended": extended}
    return {**evaluate(outcomes, window, share=share), "window": list(window), "extended": extended}


def needs_extension(outcomes: list[dict], months: tuple[str, str] = EVALUATION_MONTHS) -> bool:
    scored = [o for o in outcomes if months[0] <= o["entry_month"] <= months[1]
              and o["status"] in SCORED and o.get("score") is not None]
    return len(scored) < MIN_POSITIONS or len({o["entry_month"] for o in scored}) < MIN_ACTIVE_MONTHS


# ---- behaviour pin (a settled past window: no dependence on future exchange calendars) --

FIXTURE_MONTHS = ("2023-11", "2025-10")
FIXTURE_EXTENDED = ("2023-11", "2026-04")
FIXTURE_COMPLETED = "2026-09-30"


def _label(entry: str, exit_: str, side: str, gross: float, residual: float, scale) -> dict:
    outcome = {"entry_date": entry, "exit_date": exit_, "gross_return_pct": gross, "residual_return_pct": residual,
               "costs_pct": 0.2, "market_return_pct": 1.0, "beta": 1.0}
    reward = {"schema_version": POLICY["reward_version"], "risk_scale": {"risk_scale_pct": scale}}
    return {"learning_basis": POLICY["learning_basis"], "arms": {side: {"outcome": outcome, "reward": reward}}}


def behavior_fixture() -> dict:
    """A synthetic ledger covering every scope, score, mode and verdict path, on past sessions."""
    registered = datetime(2023, 11, 6, 22, 0, tzinfo=timezone.utc)
    sessions = window_sessions(FIXTURE_MONTHS)
    entries = [s for s in sessions if s[8:] in ("03", "10", "17", "24")][:96]
    positions, cases = [], []
    for k, entry in enumerate(entries):
        side = "LONG" if k % 3 else "SHORT"
        due = due_session(entry)
        gross = ((k * 37) % 23 - 9) / 4.0
        notional, cost = 926.0, 0.926
        position = {"position_id": f"p{k:03d}", "ticker": f"T{k % 54:02d}", "side": side,
                    "source_decision_id": f"d{k:03d}", "source_intent_id": f"i{k:03d}", "entry_market_date": entry,
                    "entry_price": 100.0, "paper_units": 9.26, "entry_notional_usd": notional,
                    "entry_cost_usd": cost, "selector_version": POLICY["selector_version"],
                    "execution_contract": POLICY["execution_contract"], "has_commitment": True,
                    "decision_recorded_at": (registered + timedelta(days=1 + k)).isoformat(),
                    "snapshot_late": k in (13, 14)}
        exit_ = {"status": "CLOSED", "exit_market_date": due, "exit_price": 100.0 * (1 + gross / 100),
                 "exit_cost_usd": cost, "realized_pnl_usd": notional * gross / 100 - 2 * cost,
                 "exit_reason": "holding limit"}
        label = _label(entry, due, SIDES[side], gross, gross - 0.3, 3.0 + k % 5 / 2)
        if k == 5:
            exit_["exit_market_date"] = session_offset(due, 2)                    # late exit
        if k == 6:
            exit_["exit_market_date"] = session_offset(due, -5)                   # early exit
        if k == 7:
            exit_["realized_pnl_usd"] += notional * 0.06 / 100                    # 0.06 pp: return mismatch
        if k == 8:
            exit_["realized_pnl_usd"] += notional * 0.04 / 100                    # 0.04 pp: within tolerance
        if k == 9:
            exit_["realized_pnl_usd"] = None                                       # invalid ledger inputs
        if k == 10:
            exit_ = None                                                           # never settled: unsettled
        if k == 11:
            label = None                                                           # never labelled: excluded
        if k == 12:
            label["arms"][SIDES[side]]["outcome"]["entry_date"] = session_offset(entry, 1)   # entry mismatch
        if k == 17:
            exit_["realized_pnl_usd"] += notional * 0.0499 / 100                  # just inside the tolerance
        if k == 18:
            exit_["realized_pnl_usd"] += notional * 0.0501 / 100                  # just outside it
        if k == 15:
            del label["arms"][SIDES[side]]["reward"]["risk_scale"]                 # label incomplete
        if k == 16:
            label["arms"][SIDES[side]]["outcome"]["residual_return_pct"] = float("nan")
        positions.append(position)
        cases.append((position, exit_, label))
    outcomes = [score(p, e, lab, FIXTURE_COMPLETED) for p, e, lab in cases]
    retry_due = due_session(entries[0])
    retry = [score(cases[0][0], None, cases[0][2], session_offset(retry_due, k)) for k in (9, 10)]
    retry += [score(cases[0][0], None, None, session_offset(retry_due, k)) for k in (9, 10)]
    extras = [
        {**positions[0], "position_id": "x1", "selector_version": "source-report-classification"},
        {**positions[0], "position_id": "x2", "selector_version": "fundamental-score-paper-v1"},
        {**positions[0], "position_id": "x3", "selector_version": None},
        {**positions[0], "position_id": "x4", "selector_version": "agent-intelligence.v3"},      # in scope (ITT)
        {**positions[0], "position_id": "x5", "execution_contract": None},
        {**positions[0], "position_id": "x6", "has_commitment": False},
        {**positions[0], "position_id": "x7", "decision_recorded_at": registered.isoformat()},
        {**positions[0], "position_id": "x8", "decision_recorded_at": (registered - timedelta(seconds=1)).isoformat()},
        {**positions[0], "position_id": "x9", "entry_market_date": "2023-10-31"},
        {**positions[0], "position_id": "x10", "entry_market_date": "2025-11-03"},
    ]
    modes = [{"occurred_at": "2023-10-02T12:00:00+00:00", "before": "RESEARCH", "after": "PAPER"},
             # EST session 2024-11-15 (stage 22:00-04:00 UTC): off at 16:59 ET, on at 17:01 ET: not counted
             {"occurred_at": "2024-11-15T21:59:00+00:00", "before": "PAPER", "after": "RESEARCH"},
             {"occurred_at": "2024-11-15T22:01:00+00:00", "before": "RESEARCH", "after": "PAPER"},
             # EDT session 2024-07-12 (stage 21:00-03:00 UTC): off at 17:01 ET, on at 17:30 ET: not counted
             {"occurred_at": "2024-07-12T21:01:00+00:00", "before": "PAPER", "after": "RESEARCH"},
             {"occurred_at": "2024-07-12T21:30:00+00:00", "before": "RESEARCH", "after": "PAPER"},
             # EDT session 2024-07-15: off at 23:01 ET (03:01 UTC), after its stage: counted, as is 07-16
             {"occurred_at": "2024-07-16T03:01:00+00:00", "before": "PAPER", "after": "RESEARCH"},
             {"occurred_at": "2024-07-16T12:00:00+00:00", "before": "RESEARCH", "after": "PAPER"},
             # EST boundaries: on at 16:59:30 ET counts; off at 22:59:30 ET does not; on at exactly 17:00 counts;
             # off at exactly 23:00 ET does not
             {"occurred_at": "2024-12-06T17:00:00+00:00", "before": "PAPER", "after": "RESEARCH"},
             {"occurred_at": "2024-12-06T21:59:30+00:00", "before": "RESEARCH", "after": "PAPER"},
             {"occurred_at": "2024-12-10T03:59:30+00:00", "before": "PAPER", "after": "RESEARCH"},
             {"occurred_at": "2024-12-10T14:00:00+00:00", "before": "RESEARCH", "after": "PAPER"},
             {"occurred_at": "2024-12-11T17:00:00+00:00", "before": "PAPER", "after": "RESEARCH"},
             {"occurred_at": "2024-12-11T22:00:00+00:00", "before": "RESEARCH", "after": "PAPER"},
             {"occurred_at": "2024-12-13T04:00:00+00:00", "before": "PAPER", "after": "RESEARCH"},
             {"occurred_at": "2024-12-13T14:00:00+00:00", "before": "RESEARCH", "after": "PAPER"}]
    pauses = [{"occurred_at": "2025-03-03T12:00:00+00:00", "before": False, "after": True},
              {"occurred_at": "2025-03-10T12:00:00+00:00", "before": True, "after": False},
              {"occurred_at": "2025-04-02T23:00:00+00:00", "before": False, "after": True},     # from 19:00 EDT
              {"occurred_at": "2025-04-03T12:00:00+00:00", "before": True, "after": False}]
    return {"registered": registered, "positions": positions + extras, "outcomes": outcomes, "retry": retry,
            "modes": modes, "pauses": pauses, "sessions": sessions}


@lru_cache(maxsize=1)
def behavior_digest() -> str:
    fx = behavior_fixture()

    def r8(x):
        return None if x is None or not math.isfinite(x) else round(float(x), 8)
    months, extended = FIXTURE_MONTHS, FIXTURE_EXTENDED
    scopes = [scope(p, fx["registered"], months) for p in fx["positions"]]
    share = mode_share(fx["modes"], fx["pauses"], fx["sessions"], "PAPER", False)
    outcomes = fx["outcomes"]
    scored = [o for o in outcomes if o.get("score") is not None]
    later = {**scored[0], "entry_month": "2025-11", "score": 100.0}        # an extension-month outcome
    pool = outcomes + [later]

    def shift(delta):
        return [{**o, "score": o["score"] + delta} if o.get("score") is not None else o for o in pool]

    clean = [o for o in scored if not o.get("snapshot_late")]
    gone = {**clean[0], "status": "EXCLUDED_MISSING_LABEL", "score": None}
    tail = sorted(clean, key=lambda o: o["score"])
    tail_cut = [{**o, "status": "EXCLUDED_MISSING_LABEL", "score": None} if i < 6 else {**o, "score": o["score"] + 0.9}
                for i, o in enumerate(tail)] * 2         # the worst losers excluded: the floor still applies
    fragile = [{**o, "status": "EXCLUDED_MISSING_LABEL", "score": None} if i % 12 == 5 else {**o, "score": o["score"] + 0.16}
               for i, o in enumerate(clean * 2)]                # passes alone, not with exclusions at the worst score
    twelve = [o for o in scored if o["entry_month"] in sorted({x["entry_month"] for x in scored})[:12]] * 2
    cases = {
        "base": evaluate(pool, months, share=share),
        "pass": evaluate(shift(4.0), months, share=share),
        "negative": evaluate(shift(-4.0), months, share=share),
        "pass_not_robust": evaluate(fragile, months, share=1.0),
        "tail_excluded": evaluate(tail_cut, months, share=1.0),
        "share_below": evaluate(pool, months, share=0.8999),
        "share_at": evaluate(pool, months, share=0.9),
        "exceptions_at_limit": evaluate((clean * 2)[:90] + [gone] * 10, months, share=1.0),      # exactly 0.1
        "exceptions_above": evaluate((clean * 17)[:899] + [gone] * 101, months, share=1.0),     # 0.101
        "short": evaluate(pool[:20], months, share=share),
        "sixty": evaluate(scored[:60], months, share=share),
        "twelve_months": evaluate(twelve, months, share=share),
        "late": decide(pool, extended=False, share=share, registered_late=True, months=months,
                       extended_months=extended),
        "extended": decide(pool, extended=True, share=share, registered_late=False, months=months,
                           extended_months=extended),
    }
    return digest(json_round({
        "scopes": scopes, "share": share, "outcomes": outcomes, "retry": fx["retry"], "cases": cases,
        "extension": [needs_extension(o, months) for o in (
            outcomes, outcomes[:20], scored[:60], scored[:59], twelve, twelve[:-len(twelve) // 2],
            scored[:59] + [later], scored[:59] + [{**later, "entry_month": "2023-10"}],
            scored[:59] + [{**scored[59], "score": None}])],
        "share_without_pauses": mode_share(fx["modes"], [], fx["sessions"], "PAPER", False),
        "share_no_events": [mode_share([], [], fx["sessions"][:5], m, p) for m, p in
                            (("PAPER", False), ("PAPER", True), ("RESEARCH", False))],
        "share_initial": [mode_share([{"occurred_at": "2023-11-08T12:00:00+00:00", "before": "RESEARCH",
                                       "after": "PAPER"}], [], fx["sessions"][:8], "PAPER", False),
                          mode_share([], [{"occurred_at": "2023-11-08T12:00:00+00:00", "before": True, "after": False}],
                                     fx["sessions"][:8], "PAPER", False)]}, r8))


def json_round(value, r):
    if isinstance(value, float):
        return r(value)
    if isinstance(value, dict):
        return {str(k): json_round(v, r) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_round(v, r) for v in value]
    return value


# ---- provenance ------------------------------------------------------------------

def missing_sources() -> list[str]:
    root = Path(__file__).resolve().parents[1]
    return [p for p in DECISION_SOURCES if not (root / p).is_file()]


def source_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    parts = []
    for p in DECISION_SOURCES:
        try:
            parts.append((root / p).read_bytes())
        except FileNotFoundError:
            parts.append(b"\0MISSING:" + p.encode() + b"\0")
    return hashlib.sha256(b"".join(parts)).hexdigest()


def live_policy() -> dict:
    """The live code's values of the registered policy fields (a difference is recorded, never fatal).
    The selector and contract are read from each position instead (snapshot)."""
    from .. import agent_trading as at
    from . import bandit, learning, outcomes, reward
    return {"selector_version": POLICY["selector_version"], "execution_contract": POLICY["execution_contract"],
            "bandit_version": bandit.VERSION, "confidence_z": bandit.CONFIDENCE_Z, "reward_version": reward.VERSION,
            "learning_basis": learning.LEARNING_BASIS, "learning_contract": outcomes.CONTRACT,
            "holding_sessions": at.HOLDING_SESSIONS, "fill_cost_bps": at.FILL_COST_BPS,
            "starting_equity_usd": at.STARTING_EQUITY_USD, "max_gross_pct": at.MAX_GROSS_PCT,
            "target_position_pct": at.TARGET_POSITION_PCT}


def code_version() -> dict:
    return {"git_sha": os.getenv("GITHUB_SHA"), "decision_sources_sha256": source_fingerprint(),
            "evaluation_behavior_sha256": EVALUATION_BEHAVIOR_SHA256}


# ---- database reads ------------------------------------------------------------

_COLS = fvf._COLS
LEDGER_SQL = """SELECT p.position_id::text, p.ticker, p.side, p.source_decision_id::text, p.source_intent_id::text,
       p.entry_market_date::text, p.entry_price, p.paper_units, p.entry_notional_usd, p.entry_cost_usd,
       p.status, p.exit_market_date::text, p.exit_price, p.exit_cost_usd, p.realized_pnl_usd, p.exit_reason,
       i.risk_snapshot->'selector'->>'version', i.risk_snapshot->>'execution_contract',
       (i.risk_snapshot ? 'holding_commitment'), d.recorded_at::text
FROM agent_paper_positions p
JOIN agent_trade_intents i ON i.intent_id = p.source_intent_id
JOIN agent_lab_decisions d ON d.decision_id = p.source_decision_id
ORDER BY p.entry_market_date, p.position_id"""
LEDGER_COLUMNS = ("position_id", "ticker", "side", "source_decision_id", "source_intent_id", "entry_market_date",
                  "entry_price", "paper_units", "entry_notional_usd", "entry_cost_usd", "status", "exit_market_date",
                  "exit_price", "exit_cost_usd", "realized_pnl_usd", "exit_reason", "selector_version",
                  "execution_contract", "has_commitment", "decision_recorded_at")


def _table(conn, name: str) -> bool:
    return conn.execute("SELECT to_regclass(%s)", (name,)).fetchone()[0] is not None


def ledger_rows(conn) -> list[dict]:
    """Every ledger position (empty when PAPER was never initialized); never runs DDL."""
    if not (_table(conn, "agent_paper_positions") and _table(conn, "agent_trade_intents")):
        return []
    return [dict(zip(LEDGER_COLUMNS, r)) for r in conn.execute(LEDGER_SQL).fetchall()]


def current_mode(conn) -> str | None:
    if not _table(conn, "agent_trading_settings"):
        return None
    row = conn.execute("SELECT mode FROM agent_trading_settings WHERE singleton").fetchone()
    return row[0] if row else None


def current_paused(conn) -> bool | None:
    if not _table(conn, "operator_controls"):
        return None
    row = conn.execute("SELECT capture_paused FROM operator_controls WHERE singleton").fetchone()
    return bool(row[0]) if row else None


def mode_events(conn) -> list[dict]:
    return [{"occurred_at": r[0], "before": r[1], "after": r[2]} for r in conn.execute(
        """SELECT occurred_at::text, details->>'before', details->>'after' FROM operator_audit
           WHERE event='TRADING_MODE_CHANGED' ORDER BY id""").fetchall()]


def pause_events(conn) -> list[dict]:
    return [{"occurred_at": r[0], "before": r[1], "after": r[2]} for r in conn.execute(
        """SELECT occurred_at::text, (details->>'before')::boolean, (details->>'after')::boolean FROM operator_audit
           WHERE event='CAPTURE_PAUSE_CHANGED' ORDER BY id""").fetchall()]


def share_through(conn, months: tuple[str, str], through: str | None = None) -> float | None:
    return mode_share(mode_events(conn), pause_events(conn), window_sessions(months, through), current_mode(conn),
                      current_paused(conn))


def labels_for(conn, decision_ids) -> dict:
    if not decision_ids:
        return {}
    return {r[0]: r[1] for r in conn.execute(
        """SELECT request_key, payload FROM research_evidence_records WHERE kind=%s AND request_key=ANY(%s)
           AND payload->>'learning_basis'=%s""", (LABEL_KIND, list(decision_ids), POLICY["learning_basis"])).fetchall()}


def _records(conn, kind: str, prefix: str) -> dict:
    return {r[0]: dict(zip(_COLS, r)) for r in conn.execute(
        """SELECT request_key,record_id,content_hash,payload,recorded_at::text FROM research_evidence_records
           WHERE kind=%s AND payload->>'forward_id'=%s AND left(request_key, %s)=%s ORDER BY request_key""",
        (kind, FORWARD_ID, len(prefix), prefix)).fetchall()}


def snapshots(conn, which: str) -> dict:
    """position_id -> its entry or exit copy."""
    return {k.rsplit(":", 1)[-1]: v for k, v in _records(conn, DECISION_KIND, f"{FORWARD_ID}:{which}:").items()}


def nights(conn) -> dict:
    return {k.rsplit(":", 1)[-1]: v for k, v in _records(conn, DECISION_KIND, f"{FORWARD_ID}:night:").items()}


def outcomes(conn) -> dict:
    return {k.rsplit(":", 1)[-1]: v["payload"] for k, v in _records(conn, OUTCOME_KIND, f"{FORWARD_ID}:outcome:").items()}


def checks(conn, statuses=None) -> list[dict]:
    rows = _records(conn, CHECK_KIND, f"{FORWARD_ID}:").values()
    return [r for r in rows if statuses is None or r["payload"].get("status") in statuses]


def final_evaluation(conn) -> dict | None:
    row = conn.execute("SELECT payload FROM research_evidence_records WHERE kind=%s AND request_key=%s",
                       (EVALUATION_KIND, f"{FORWARD_ID}:final")).fetchone()
    return row[0] if row else None


def protocol_record(conn) -> dict | None:
    from .. import research_store
    return research_store.get(conn, PROTOCOL_KIND, FORWARD_ID)


def superseded_protocols(conn) -> list[str]:
    """Other paper-ledger evaluations registered and never finalized (a later version after seeing data)."""
    return [r[0] for r in conn.execute(
        """SELECT p.request_key FROM research_evidence_records p WHERE p.kind=%s
           AND p.request_key LIKE 'agent-paper-ledger.%%' AND p.request_key<>%s
           AND NOT EXISTS (SELECT 1 FROM research_evidence_records e WHERE e.kind=%s
                           AND e.request_key=p.request_key || ':final') ORDER BY 1""",
        (PROTOCOL_KIND, FORWARD_ID, EVALUATION_KIND)).fetchall()]


def extended(conn) -> bool:
    return any(c["payload"].get("status") == "EXTENDED_TO_36_MONTHS" for c in checks(conn))


def recorded_checks(conn) -> dict:
    rows = checks(conn)
    return {s: sum(r["payload"].get("status") == s for r in rows) for s in RECORDED_STATUSES}


def sticky_attention(conn, now: datetime) -> list[str]:
    out = []
    for r in checks(conn, LOSS_STATUSES):
        recorded = fvf._recorded_at(r)
        if timedelta(0) <= now - recorded <= timedelta(days=STICKY_DAYS):
            p = r["payload"]
            out.append(":".join(str(x) for x in (p["status"], p.get("position_id"), p.get("field"), p.get("session"))
                                if x not in (None, "")))
    return out


# ---- database writes (evidence only) ---------------------------------------------

def _save(conn, kind: str, key: str, payload: dict) -> bool:
    from .. import research_store
    if research_store.get(conn, kind, key):
        return False
    research_store.save(conn, kind, key, clean({"forward_id": FORWARD_ID, **payload}))
    return True


def _check(conn, key: str, payload: dict) -> bool:
    return _save(conn, CHECK_KIND, f"{FORWARD_ID}:{key}", payload)


def _register(conn) -> dict:
    from .. import research_store
    try:
        return research_store.save(conn, PROTOCOL_KIND, FORWARD_ID, PROTOCOL)
    except ValueError as exc:
        raise ProtocolChanged("FORWARD_PROTOCOL_CHANGED") from exc


def _differs(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) \
            and not isinstance(b, bool):
        return abs(float(a) - float(b)) > 1e-9 * max(1.0, abs(float(a)))
    return a != b


def snapshot(conn, read_conn, completed: str, registered_at: datetime) -> dict:
    """Copy new in-scope positions and new exits append-only; flag ledger rows that changed."""
    rows = ledger_rows(read_conn)
    read_conn.rollback()
    entries, exits = snapshots(conn, "entry"), snapshots(conn, "exit")
    live = {r["position_id"]: r for r in rows}
    stats = {"ledger_positions": len(rows), "in_scope": 0, "new_entries": 0, "new_exits": 0, "mutated": [],
             "late": [], "drift": [], "scope_excluded": [], "out_of_scope": {s: 0 for s in OUT_OF_SCOPE}}
    for row in rows:
        pid = row["position_id"]
        reason = None if pid in entries else scope(row, registered_at, EXTENDED_MONTHS)   # once copied, kept
        if reason:
            stats["out_of_scope"][reason] += 1
            recorded = row.get("decision_recorded_at")
            if (reason in ("OTHER_SELECTOR", "LEGACY_CONTRACT") and recorded and fvf._parse(recorded) > registered_at
                    and EXTENDED_MONTHS[0] <= str(row["entry_market_date"])[:7] <= EXTENDED_MONTHS[1]):
                if _check(conn, f"SCOPE_EXCLUDED_AFTER_REGISTRATION:{pid}", {
                        "status": "SCOPE_EXCLUDED_AFTER_REGISTRATION", "position_id": pid, "field": reason,
                        "session": completed}):
                    stats["scope_excluded"].append(f"{pid}:{reason}")
            continue
        stats["in_scope"] += 1
        for field in ("selector_version", "execution_contract"):
            if row[field] != POLICY[field] and _check(conn, f"POLICY_VERSION_CHANGED:{field}:{row[field]}", {
                    "status": "POLICY_VERSION_CHANGED", "field": field, "registered": POLICY[field],
                    "live": row[field], "position_id": pid, "session": completed}):
                stats["drift"].append(f"{field}:{row[field]}")
        if pid not in entries:
            late = completed > session_offset(row["entry_market_date"], 1)
            _save(conn, DECISION_KIND, f"{FORWARD_ID}:entry:{pid}", {
                **{k: row[k] for k in (*ENTRY_FIELDS, *SCOPE_FIELDS)}, "position_id": pid,
                "first_seen_session": completed, "snapshot_late": late})
            stats["new_entries"] += 1
            if late:
                stats["late"].append(pid)
                _check(conn, f"SNAPSHOT_LATE:{pid}", {"status": "SNAPSHOT_LATE", "position_id": pid,
                                                      "session": completed, "entry": row["entry_market_date"]})
        if row["status"] == "CLOSED" and pid not in exits:
            _save(conn, DECISION_KIND, f"{FORWARD_ID}:exit:{pid}", {
                **{k: row[k] for k in EXIT_FIELDS}, "position_id": pid, "first_seen_session": completed})
            stats["new_exits"] += 1
    for pid, record in entries.items():
        row, p = live.get(pid), record["payload"]
        changed = (["ROW_MISSING"] if row is None else
                   [k for k in (*ENTRY_FIELDS, *SCOPE_FIELDS) if _differs(row[k], p.get(k))])
        exit_record = exits.get(pid)
        if row is not None and exit_record:
            changed += [k for k in EXIT_FIELDS if _differs(row[k], exit_record["payload"].get(k))]
        for field in changed:
            if _check(conn, f"LEDGER_MUTATED:{pid}:{field}", {"status": "LEDGER_MUTATED", "position_id": pid,
                                                               "field": field, "session": completed}):
                stats["mutated"].append(f"{pid}:{field}")
    conn.commit()
    return stats


def record_night(conn, completed: str, registered_at: datetime, mode: str | None, night: dict) -> dict:
    """The nightly coverage record, missed nights since the last one, and a code change (once each)."""
    earlier = nights(conn)
    fingerprint = source_fingerprint()
    previous = max((s for s in earlier if s < completed), default=None)
    first = session_offset(month_end(_previous_month(EVALUATION_MONTHS[0])), 1)
    registered_session = latest_completed_session(registered_at)
    missed = []
    if previous:
        gap = [s for s in session_dates(session_offset(previous, 1), completed)[:-1]
               if first <= s <= month_end(EXTENDED_MONTHS[1]) and s > registered_session and s not in earlier]
        for s in gap:
            if _check(conn, f"NIGHT_MISSED:{s}", {"status": "NIGHT_MISSED", "session": s}):
                missed.append(s)
    changed = bool(previous) and earlier[previous]["payload"].get("decision_sources_sha256") not in (None, fingerprint)
    if changed:
        _check(conn, f"DECISION_SOURCES_CHANGED:{completed}", {"status": "DECISION_SOURCES_CHANGED",
                                                               "session": completed, "sha256": fingerprint})
    _save(conn, DECISION_KIND, f"{FORWARD_ID}:night:{completed}", {
        "session": completed, "mode": mode, "ledger_positions": night["ledger_positions"],
        "in_scope": night["in_scope"], "decision_sources_sha256": fingerprint})
    conn.commit()
    return {"missed": missed, "decision_sources_changed": changed}


def score_pending(conn, read_conn, completed: str) -> dict:
    """Write each in-scope position's outcome once, from its label and copies."""
    entries, exits, done = snapshots(conn, "entry"), snapshots(conn, "exit"), outcomes(conn)
    pending = {pid: rec["payload"] for pid, rec in entries.items() if pid not in done}
    labels = labels_for(read_conn, {p["source_decision_id"] for p in pending.values()})
    read_conn.rollback()
    stats = {"scored": 0, "excluded": [], "waiting": 0, "errors": []}
    for pid, entry in sorted(pending.items()):
        try:
            exit_ = (exits.get(pid) or {}).get("payload")
            result = score(entry, exit_, labels.get(entry["source_decision_id"]), completed)
            if result is None:
                stats["waiting"] += 1
                continue
            _save(conn, OUTCOME_KIND, f"{FORWARD_ID}:outcome:{pid}", {**result, "code": code_version()})
            conn.commit()
            if result["status"] in SCORED:
                stats["scored"] += 1
            else:
                stats["excluded"].append(f"{pid}:{result['status']}")
        except Exception as exc:
            with suppress(Exception):
                conn.rollback()
            stats["errors"].append({"position_id": pid, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
    return stats


def _window_resolved(conn, months: tuple[str, str]) -> bool:
    entries, done = snapshots(conn, "entry"), outcomes(conn)
    return all(pid in done for pid, rec in entries.items()
               if months[0] <= rec["payload"]["entry_market_date"][:7] <= months[1])


def maybe_final(conn, read_conn, completed: str, registered: dict, stats: dict) -> dict | None:
    """Extend once on counts, or write the final record once; returns the final record if any."""
    from .. import research_store
    final = final_evaluation(conn)
    if final or stats["errors"]:
        return final
    is_extended = extended(conn)
    months = EXTENDED_MONTHS if is_extended else EVALUATION_MONTHS
    if completed <= month_end(months[1]) or not _window_resolved(conn, months):
        return None
    recorded = list(outcomes(conn).values())
    late = fvf._parse(registered["recorded_at"]) >= fvf.session_open(REGISTER_BY_SESSION)
    if not is_extended and not late and needs_extension(recorded, EVALUATION_MONTHS):
        _check(conn, "EXTENDED_TO_36_MONTHS", {"status": "EXTENDED_TO_36_MONTHS", "session": completed})
        conn.commit()
        return None
    share = share_through(read_conn, months)
    read_conn.rollback()
    integrity = {s: sorted(r["request_key"] for r in checks(conn, (s,))) for s in LOSS_STATUSES}
    research_store.save(conn, EVALUATION_KIND, f"{FORWARD_ID}:final", clean({
        "forward_id": FORWARD_ID, "evaluated_through": completed, "protocol_sha256": PROTOCOL_SHA256,
        **decide(recorded, extended=is_extended, share=share, registered_late=late, months=EVALUATION_MONTHS,
                 extended_months=EXTENDED_MONTHS), "integrity": integrity,
        "code": code_version()}))
    conn.commit()
    return final_evaluation(conn)


# ---- run harness -----------------------------------------------------------------

def run_daily(*, now: datetime | None = None, clock=None) -> dict:
    """Snapshot the ledger, score resolved positions, and evaluate once at the horizon."""
    from .. import db
    from ..admin_panel.store import audit

    if clock is None:
        clock = (lambda: now) if now is not None else _utcnow
    now = now or clock()
    if now.tzinfo is None:
        raise ValueError("FORWARD_TIMESTAMP_NOT_AWARE")
    completed = latest_completed_session(now)
    receipt = {"stage": "paper_ledger_forward", "forward_id": FORWARD_ID, "completed_session": completed,
               "promotion": "NOT_AUTHORIZED", "broker_submission": False}
    if os.getenv("GITHUB_REF") != "refs/heads/main":
        return {**receipt, "status": "REFUSED_NON_MAIN", "reason_code": os.getenv("GITHUB_REF") or "UNSET"}
    try:
        return _run_locked(receipt, completed, clock, db, audit)
    except Exception as exc:
        return {**receipt, "status": "FAILED", "reason_code": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _run_locked(receipt, completed, clock, db, audit) -> dict:
    from psycopg import IsolationLevel
    with db.connect() as lock:
        lock.autocommit = True
        if not lock.execute("SELECT pg_try_advisory_lock(hashtextextended(%s,0))", (FORWARD_ID,)).fetchone()[0]:
            return {**receipt, "status": "BUSY"}
        try:
            audit(lock, ACTOR, "PAPER_LEDGER_FORWARD_STAGE_STARTED", {**receipt, "status": "STARTED"})
            with db.connect() as conn, db.connect() as read_conn:
                # Every read transaction begins REPEATABLE READ READ ONLY; nothing is left on the session.
                read_conn.isolation_level = IsolationLevel.REPEATABLE_READ
                read_conn.read_only = True
                registered = _register(conn)
                conn.commit()
                receipt.update(protocol_sha256=PROTOCOL_SHA256, protocol_record_id=registered["record_id"],
                               protocol_registered_at=registered["recorded_at"])
                registered_at = fvf._parse(registered["recorded_at"])
                drift = {k: v for k, v in live_policy().items() if _differs(v, POLICY[k])}
                new_drift = {}
                for field, value in drift.items():
                    if _check(conn, f"POLICY_VERSION_CHANGED:{field}:{value}", {
                            "status": "POLICY_VERSION_CHANGED", "field": field, "registered": POLICY[field],
                            "live": value, "session": completed}):
                        new_drift[field] = value
                conn.commit()
                night = snapshot(conn, read_conn, completed, registered_at)
                mode = current_mode(read_conn)
                read_conn.rollback()
                coverage = record_night(conn, completed, registered_at, mode, night)
                try:
                    if behavior_digest() != EVALUATION_BEHAVIOR_SHA256:
                        raise ProtocolChanged("EVALUATION_BEHAVIOR_CHANGED")   # never blocks the snapshot above
                    stats = score_pending(conn, read_conn, completed)
                except Exception as exc:
                    for c in (conn, read_conn):
                        with suppress(Exception):
                            c.rollback()
                    stats = {"scored": 0, "excluded": [], "waiting": 0,
                             "errors": [{"error": f"{type(exc).__name__}: {str(exc)[:200]}"}]}
                final = maybe_final(conn, read_conn, completed, registered, stats)
                sticky = sticky_attention(conn, clock())
            gone = missing_sources()
            attention = bool(night["mutated"] or night["late"] or night["drift"] or night["scope_excluded"]
                             or new_drift or coverage["missed"] or coverage["decision_sources_changed"]
                             or stats["errors"] or stats["excluded"] or sticky or gone)
            receipt.update(status="ATTENTION" if attention else "OK", snapshot=night, coverage=coverage,
                           outcomes=stats, mode=mode, policy_drift=drift, sticky_attention=sticky,
                           missing_sources=gone, evaluation_final=bool(final))
        except ProtocolChanged as exc:
            receipt.update(status="PROTOCOL_CHANGED", reason_code=str(exc))
        except Exception as exc:
            receipt.update(status="FAILED", reason_code=f"{type(exc).__name__}: {str(exc)[:200]}")
        finally:
            try:
                audit(lock, ACTOR, "PAPER_LEDGER_FORWARD_STAGE_FINISHED",
                      clean({**receipt, "status": receipt.get("status", "FAILED")}))
            except Exception:
                receipt["audit_failed"] = True
                if receipt.get("status") == "OK":
                    receipt["status"] = "ATTENTION"
            with suppress(Exception):
                lock.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (FORWARD_ID,))
        return receipt


# ---- read-only view --------------------------------------------------------------

def summary(conn, *, now: datetime | None = None) -> dict:
    """Coverage only before the final record: no mean, interval or 'on track' signal."""
    now = now or _utcnow()
    conn.execute("SET TRANSACTION READ ONLY")
    completed = latest_completed_session(now)
    registered = protocol_record(conn)
    entries, done, final = snapshots(conn, "entry"), outcomes(conn), final_evaluation(conn)
    night_records = nights(conn)
    is_extended = extended(conn)
    months = EXTENDED_MONTHS if is_extended else EVALUATION_MONTHS
    in_window = [o for o in done.values() if months[0] <= o["entry_month"] <= months[1]]
    by_status = {}
    for o in in_window:
        key = o["status"] if o["status"] not in SCORED else f"SCORED:{o.get('reconciliation')}"
        by_status[key] = by_status.get(key, 0) + 1
    scored = [o for o in in_window if o["status"] in SCORED]
    excluded = [o for o in in_window if o["status"] in EXCLUDED]
    exceptions = len(excluded) + sum(1 for o in scored if o.get("snapshot_late"))
    share = share_through(conn, months, completed)
    deadline = fvf.session_open(REGISTER_BY_SESSION)
    latest = max(night_records) if night_records else None
    fingerprints = {n["payload"].get("decision_sources_sha256") for n in night_records.values()} - {None}
    out = {
        "status": "OK", "forward_id": FORWARD_ID, "protocol_sha256": PROTOCOL_SHA256,
        "protocol_registered": bool(registered),
        "protocol_registered_at": registered["recorded_at"] if registered else None,
        "register_by": deadline.isoformat(),
        "registration_overdue": not registered and now >= deadline,
        "registered_late": bool(registered) and fvf._parse(registered["recorded_at"]) >= deadline,
        "evaluation_months": list(months), "extended": is_extended, "completed_session": completed,
        "latest_snapshot_session": latest,
        "current_mode": night_records[latest]["payload"].get("mode") if latest else None,
        "paper_mode_share_so_far": None if share is None else round(share, 4),
        "positions_seen": len(entries), "positions_resolved": len(done), "outcomes_by_status": by_status,
        "scored_positions": len(scored), "active_months": len({o["entry_month"] for o in scored}),
        "exception_share_so_far": round(exceptions / ((len(scored) + len(excluded)) or 1), 4),
        "minimum": {"positions": MIN_POSITIONS, "active_months": MIN_ACTIVE_MONTHS},
        "decision_code_versions": len(fingerprints), "recorded_checks": recorded_checks(conn),
        "superseded_protocols": superseded_protocols(conn),
        "sticky_attention": sticky_attention(conn, now),
        "evaluation": ({**final, "final": True} if final else
                       {"status": "EVALUATION_DUE" if completed > month_end(months[1]) else "PENDING", "final": False}),
        "not_evidence": "ledger P&L, open marks and the weekly account-vs-SPY audit; only the final record judges paper",
        "use": "INFORMATION_ONLY", "promotion": "NOT_AUTHORIZED", "broker_submission": False,
    }
    conn.rollback()
    return clean(out)


PAPER_LEDGER_KEYS = ("protocol_registered", "protocol_registered_at", "register_by", "registration_overdue",
                     "registered_late", "evaluation_months", "extended", "latest_snapshot_session", "current_mode",
                     "paper_mode_share_so_far", "positions_seen", "positions_resolved", "outcomes_by_status",
                     "scored_positions", "active_months", "exception_share_so_far", "decision_code_versions",
                     "recorded_checks", "superseded_protocols", "sticky_attention", "evaluation")


def compact(view: dict) -> dict:
    return {k: view.get(k) for k in PAPER_LEDGER_KEYS}
