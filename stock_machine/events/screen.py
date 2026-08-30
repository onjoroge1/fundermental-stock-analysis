"""Strategy-aware event gates for mixed-expiration option structures.

The screen is deliberately conservative because the current mixed-expiration
valuation holds IV/dividend assumptions constant.  It only returns CLEAR when
provider coverage is both fresh and complete across the relevant window.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .store import events_in_window, latest_coverage

CALL_MIXED = {"call_calendar", "call_diagonal"}
PUT_MIXED = {"put_calendar", "put_diagonal"}
MIXED = CALL_MIXED | PUT_MIXED


@dataclass(frozen=True)
class EventScreenPolicy:
    max_snapshot_age_days: int = 3
    require_complete_earnings_coverage: bool = True
    require_complete_dividend_coverage: bool = True
    require_complete_split_coverage: bool = True
    block_earnings_through_front_expiry: bool = True
    block_call_ex_dividend_through_front_expiry: bool = True
    block_splits_through_far_expiry: bool = True


def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def _coverage_gate(coverage: dict | None, event_type: str,
                   as_of: str, required_end: str,
                   require_complete: bool,
                   policy: EventScreenPolicy) -> list[str]:
    if not require_complete:
        return []
    if not coverage:
        return [f"{event_type.lower()} coverage is missing"]
    if coverage.get("coverage_status") != "AVAILABLE":
        return [
            f"{event_type.lower()} coverage is {coverage.get('coverage_status') or 'UNKNOWN'}, not AVAILABLE"
        ]
    observed_on = coverage.get("observed_on")
    if not observed_on:
        return [f"{event_type.lower()} coverage lacks observed_on"]
    age = _days_between(observed_on, as_of)
    if age < 0:
        return [f"{event_type.lower()} coverage is future-dated"]
    if age > policy.max_snapshot_age_days:
        return [
            f"{event_type.lower()} coverage is stale ({age} days old; max {policy.max_snapshot_age_days})"
        ]
    window_start = coverage.get("window_start")
    window_end = coverage.get("window_end")
    if not window_start or not window_end:
        return [f"{event_type.lower()} coverage lacks a bounded window"]
    if window_start > as_of[:10] or window_end < required_end[:10]:
        return [
            f"{event_type.lower()} coverage does not span {as_of[:10]} through {required_end[:10]}"
        ]
    return []


def build_event_screen(conn, ticker: str, strategy_type: str,
                       front_expiration: str, far_expiration: str,
                       *, as_of: str | None = None,
                       policy: EventScreenPolicy | None = None) -> dict:
    """Return CLEAR/BLOCK plus evidence for one mixed-expiration candidate."""
    policy = policy or EventScreenPolicy()
    strategy = str(strategy_type or "").lower()
    symbol = ticker.upper()
    today = (as_of or date.today().isoformat())[:10]
    front = front_expiration[:10]
    far = far_expiration[:10]
    reasons: list[str] = []
    warnings: list[str] = []

    if strategy not in MIXED:
        return {
            "status": "BLOCK",
            "ticker": symbol,
            "strategy_type": strategy,
            "reasons": ["event screen only supports calendars/diagonals"],
            "warnings": [],
        }
    try:
        if date.fromisoformat(front) < date.fromisoformat(today):
            reasons.append("front expiration is before event-screen as_of")
        if date.fromisoformat(far) <= date.fromisoformat(front):
            reasons.append("far expiration must be after front expiration")
    except ValueError:
        reasons.append("invalid event-screen date")

    earnings_cov = latest_coverage(conn, symbol, "EARNINGS", today)
    dividend_cov = latest_coverage(conn, symbol, "EX_DIVIDEND", today)
    split_cov = latest_coverage(conn, symbol, "SPLIT", today)

    reasons.extend(_coverage_gate(
        earnings_cov, "EARNINGS", today, far,
        policy.require_complete_earnings_coverage, policy,
    ))
    reasons.extend(_coverage_gate(
        dividend_cov, "EX_DIVIDEND", today, far,
        policy.require_complete_dividend_coverage, policy,
    ))
    reasons.extend(_coverage_gate(
        split_cov, "SPLIT", today, far,
        policy.require_complete_split_coverage, policy,
    ))

    earnings_front = events_in_window(conn, symbol, "EARNINGS", today, front, today)
    earnings_far = events_in_window(conn, symbol, "EARNINGS", front, far, today)
    dividends_front = events_in_window(conn, symbol, "EX_DIVIDEND", today, front, today)
    dividends_far = events_in_window(conn, symbol, "EX_DIVIDEND", front, far, today)
    splits_far = events_in_window(conn, symbol, "SPLIT", today, far, today)

    if earnings_front and policy.block_earnings_through_front_expiry:
        reasons.append(
            "earnings event occurs on/before the short-option front expiration"
        )
    if earnings_far:
        warnings.append(
            "earnings occurs after front expiry but before far expiry; remaining-option IV may differ from constant-IV valuation"
        )

    if dividends_front:
        if strategy in CALL_MIXED and policy.block_call_ex_dividend_through_front_expiry:
            reasons.append(
                "ex-dividend date occurs on/before front expiry; short-call early-assignment risk is elevated"
            )
        else:
            warnings.append(
                "ex-dividend date occurs before front expiry; price/dividend assumptions may affect valuation"
            )
    if dividends_far:
        warnings.append(
            "ex-dividend date occurs after front expiry but before far expiry"
        )

    if splits_far and policy.block_splits_through_far_expiry:
        reasons.append(
            "announced stock split occurs before far expiry; adjusted-contract handling is outside the mixed-option model"
        )

    evidence = {
        "earnings_through_front": earnings_front,
        "earnings_front_to_far": earnings_far,
        "ex_dividend_through_front": dividends_front,
        "ex_dividend_front_to_far": dividends_far,
        "splits_through_far": splits_far,
    }
    return {
        "status": "BLOCK" if reasons else "CLEAR",
        "ticker": symbol,
        "strategy_type": strategy,
        "as_of": today,
        "front_expiration": front,
        "far_expiration": far,
        "coverage": {
            "earnings": earnings_cov,
            "ex_dividend": dividend_cov,
            "split": split_cov,
        },
        "evidence": evidence,
        "reasons": list(dict.fromkeys(reasons)),
        "warnings": list(dict.fromkeys(warnings)),
        "methodology": (
            "fail closed on missing/stale/incomplete event coverage; block earnings through front expiry, "
            "short-call ex-dividend exposure through front expiry, and splits through far expiry"
        ),
    }


def build_strategy_event_screen(conn, ticker: str, candidate: dict,
                                *, as_of: str | None = None,
                                policy: EventScreenPolicy | None = None) -> dict:
    strategy = str(candidate.get("strategy_type") or "")
    front = candidate.get("front_expiration") or candidate.get("near_expiration")
    far = candidate.get("far_expiration")
    if not front or not far:
        return {
            "status": "BLOCK",
            "ticker": ticker.upper(),
            "strategy_type": strategy,
            "reasons": ["candidate lacks front/far expiration dates"],
            "warnings": [],
        }
    return build_event_screen(
        conn, ticker, strategy, str(front), str(far), as_of=as_of, policy=policy
    )
