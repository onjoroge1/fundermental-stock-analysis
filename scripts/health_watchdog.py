"""Daily health watchdog: checks that prices, nightly jobs, research coverage and the two
pre-registered forward trackers (fair value, event strategies) are sound, and lists reviewed
financials that a newer filing has superseded (they need a re-review).

Writes a JSON summary and a Markdown report; the workflow turns problems into
one GitHub issue labelled `health-alert` (GitHub notifies the owner by email
and the mobile app) and closes it when everything recovers. Read-only: uses
the app's public endpoints and the workflow's GitHub token.

  python scripts/health_watchdog.py --json out.json --markdown out.md
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import date, datetime, timedelta, timezone

BASE_URL = os.environ.get("STOCK_MACHINE_API_BASE_URL", "https://fundermental-stock-analysis.vercel.app")
UNIVERSE = 54
COVERAGE_CRITICAL_BELOW = 45
# Workflow file, severity when its latest completed run failed.
WORKFLOWS = (
    ("daily-data-refresh.yml", "critical"),
    ("technical-setup-shadow.yml", "warning"),
    ("option-surface-capture.yml", "warning"),
    ("company-events-refresh.yml", "warning"),
    ("fair-value-forward.yml", "warning"),
    ("strategy-forward.yml", "warning"),
    ("paper-ledger-forward.yml", "warning"),
)
# Journal blockers meaning "financial data unverified": still learnable (#120).
FUNDAMENTAL_BLOCKERS = {"FINANCIAL_DEPENDENCIES_UNVERIFIED", "REQUIRED_DATA_NOT_VERIFIED", "DATA_QUALITY_NOT_PASS"}
# Without this a single workflow that stopped being scheduled (disabled, or its trigger stopped
# firing) stays green forever; every watched schedule has a run under 18h old at 13:15 UTC
# Tue-Sat. (A repository-wide 60-day inactivity disable also stops this watchdog itself.)
MAX_RUN_AGE = timedelta(hours=36)
# Forward trackers, read from GET /api/v1/agent/learning-status (the watchdog has no database).
MAX_ISOLATED_GAP_TICKERS = 3  # strategy_forward's rule: a partial session this small needs no action
# The market benchmark's stored history must reach back this far (Yahoo serves about 15 years);
# a fallback provider once replaced SPY's 15 years with 5, silently breaking every replay.
BENCHMARK = "SPY"
BENCHMARK_MIN_YEARS = 14
# Reviewed debt and balance fields, read from GET /api/v1/financial-reviews (tracker item 53).
REVIEW_FIELDS = ("issuers", "due", "not_binding", "awaiting_normalization", "filed_not_normalized")
REVIEW_GRACE_DAYS = 7  # a re-review normally lands in the owner's weekly batch; only a lagging one alerts
REVIEW_STUCK_DAYS = 3  # a filing still not normalized after this is a pipeline problem (items 40, 47), not a review
FORWARD_LABEL = {"fair_value_forward": "Fair-value forward", "strategy_forward": "Strategy forward shadow"}
FORWARD_SOURCE = {"fair_value_forward": "fair_value_sector_pe", "strategy_forward": "event_strategies"}
FORWARD_FIELDS = {
    "fair_value_forward": ("protocol_registered", "expected_decision_session", "freeze_missing", "freeze_deadline",
                           "frozen_months", "missed_months", "unrecorded_missed_months", "sticky_attention"),
    "strategy_forward": ("protocol_registered", "frozen_sessions", "missed_count", "unrecorded_missed_sessions",
                         "freeze_missing", "sticky_attention", "latest_decision_session"),
}


def check_prices(health: dict) -> dict:
    ok = health.get("status") == "HEALTHY"
    return {
        "check": "prices",
        "severity": None if ok else "critical",
        "summary": (
            f"All {health.get('current_count')} stocks current through {health.get('latest_market_date')}"
            if ok else
            f"Prices {health.get('status')}: {health.get('stale_count')} stale, "
            f"{health.get('missing_count')} missing; latest {health.get('latest_market_date')}, "
            f"expected {health.get('expected_market_date')}"
        ),
    }


def check_workflow(name: str, severity: str, runs: list[dict], *, now: datetime | None = None) -> dict:
    """runs: newest first, each {conclusion, created_at, html_url}. The latest
    completed run decides: a failed first attempt followed by a successful
    retry is healthy; with `now`, a success older than MAX_RUN_AGE means the
    schedule stopped."""
    completed = [r for r in runs if r.get("conclusion")]
    if not completed:
        return {"check": name, "severity": "warning", "summary": f"{name}: no completed runs found"}
    latest = completed[0]
    if latest["conclusion"] == "success" and now is not None and \
            now - datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00")) > MAX_RUN_AGE:
        return {"check": name, "severity": severity, "summary": f"{name}: no completed run since {latest['created_at']}",
                "url": latest.get("html_url")}
    if latest["conclusion"] == "success":
        return {"check": name, "severity": None, "summary": f"{name}: last run succeeded ({latest['created_at']})"}
    failed_streak = 0
    for r in completed:
        if r["conclusion"] == "success":
            break
        failed_streak += 1
    return {
        "check": name,
        "severity": severity,
        "summary": f"{name}: last {failed_streak} completed run(s) {latest['conclusion']}; latest {latest['created_at']}",
        "url": latest.get("html_url"),
    }


def check_coverage(decisions: list[dict], session: str) -> dict:
    """decisions: journal rows; counts the latest decision per ticker for `session`."""
    latest: dict[str, dict] = {}
    for d in decisions:
        if d.get("price_date") != session:
            continue
        prior = latest.get(d["ticker"])
        if prior is None or d.get("decided_at", "") > prior.get("decided_at", ""):
            latest[d["ticker"]] = d
    covered = len(latest)
    paper = sum(d.get("status") == "RECORDED" for d in latest.values())
    withheld = sum(
        d.get("status") != "RECORDED" and d.get("blockers") and set(d["blockers"]) <= FUNDAMENTAL_BLOCKERS
        for d in latest.values()
    )
    severity = None
    if covered < COVERAGE_CRITICAL_BELOW:
        severity = "critical"
    elif covered < UNIVERSE:
        severity = "warning"
    return {
        "check": "research_coverage",
        "severity": severity,
        "summary": (
            f"{covered}/{UNIVERSE} stocks decided for {session}; "
            f"{paper + withheld} learnable ({paper} paper-eligible, {withheld} with fundamentals withheld)"
        ),
        "covered": covered,
        "learnable": paper + withheld,
    }


def _isolated_partial(item: str) -> bool:
    status, _, rest = item.partition(":")
    count = rest.rsplit(":", 1)[-1]
    return (status in ("FROZEN_WITH_MISSING_BARS", "FROZEN_WITH_UNREAD_FILINGS") and count.isdigit()
            and int(count) <= MAX_ISOLATED_GAP_TICKERS)


def check_forward(check: str, status: dict, today) -> dict:
    """One forward tracker's health from its public status. Recorded losses stay visible for
    the trackers' own week; a miss no run has recorded means the tracker is not running.
    `keys` (each recorded loss, NOT_RUNNING, PROTOCOL_NOT_REGISTERED) go into the signature,
    so a new problem on an already-yellow tracker still notifies."""
    label = FORWARD_LABEL[check]
    gaps = [k for k in FORWARD_FIELDS[check] if k not in (status or {})]
    if gaps:
        return {"check": check, "severity": "warning", "summary": f"{label}: status fields missing ({', '.join(gaps)})",
                "keys": ["FIELDS_MISSING"]}
    reasons, keys = ([], []) if status["protocol_registered"] else (["protocol not registered"], ["PROTOCOL_NOT_REGISTERED"])
    sticky = list(status["sticky_attention"] or [])
    notes = [item for item in sticky if _isolated_partial(item)]
    losses = [item for item in sticky if item not in notes]
    reasons += losses
    keys += losses
    if check == "fair_value_forward":
        expected = status["expected_decision_session"]
        unrecorded = list(status["unrecorded_missed_months"] or [])
        if status["freeze_missing"] and expected not in (status["missed_months"] or []):
            unrecorded.append(expected)
        if unrecorded:
            reasons.append(f"not frozen and no run recorded the loss: {', '.join(unrecorded)} (deadline "
                           f"{status['freeze_deadline']} for {expected})" if expected in unrecorded else
                           f"not frozen and no run recorded the loss: {', '.join(unrecorded)}")
            keys.append("NOT_RUNNING")
        ok = (f"{label}: {len(status['frozen_months'] or [])} month(s) frozen, {len(status['missed_months'] or [])} missed; "
              f"latest month-end {expected or 'not yet due'}")
    else:
        unrecorded = list(status["unrecorded_missed_sessions"] or [])
        if unrecorded:
            reasons.append("not frozen and not yet recorded: " + ", ".join(unrecorded))
            keys.append("NOT_RUNNING")
        if status["freeze_missing"]:
            reasons.append("latest session missed its freeze deadline")
            keys.append("NOT_RUNNING")
        ok = (f"{label}: {status['frozen_sessions']} session(s) frozen, {status['missed_count']} missed; "
              f"latest {status['latest_decision_session'] or 'none yet'}"
              + (f"; isolated partial sessions: {', '.join(notes)}" if notes else ""))
    if reasons:
        return {"check": check, "severity": "warning", "summary": f"{label} needs attention: " + "; ".join(reasons),
                "keys": sorted(set(keys))}
    return {"check": check, "severity": None, "summary": ok}


PAPER_LEDGER_FIELDS = ("protocol_registered", "register_by", "registration_overdue", "registered_late",
                       "positions_seen", "scored_positions", "paper_mode_share_so_far", "exception_share_so_far",
                       "superseded_protocols", "sticky_attention", "evaluation")
PAPER_EXCEPTION_WARN = 0.05   # half the protocol's 10% data rule: drift toward DATA_COMPROMISED shows early
PAPER_MODE_WARN = 0.95        # half the protocol's 10% mode budget: drift toward MODE_INTERRUPTED shows early


def check_paper_ledger(status: dict) -> dict:
    """The pre-registered paper-ledger evaluation (item 52): registered in time, and no ledger row
    changed after its snapshot. Coverage only; the evaluation itself reports no interim estimate."""
    label = "Paper-ledger evaluation"
    gaps = [k for k in PAPER_LEDGER_FIELDS if k not in (status or {})]
    if gaps:
        return {"check": "paper_ledger_forward", "severity": "warning",
                "summary": f"{label}: status fields missing ({', '.join(gaps)})", "keys": ["FIELDS_MISSING"]}
    reasons, keys = [], []
    if status["registration_overdue"]:
        reasons.append(f"protocol not registered before {status['register_by']} (dispatch paper-ledger-forward.yml)")
        keys.append("NOT_REGISTERED")
    if status["registered_late"]:
        reasons.append("protocol registered after the deadline: no verdict will be issued")
        keys.append("REGISTERED_LATE")
    share = status["paper_mode_share_so_far"]
    if share is not None and share < PAPER_MODE_WARN:
        reasons.append(f"PAPER on with capture unpaused for only {share:.0%} of window sessions so far "
                       "(the protocol voids the verdict below 90%)")
        keys.append("MODE_SHARE_BELOW_95PCT")
    if (status["exception_share_so_far"] or 0) > PAPER_EXCEPTION_WARN:
        reasons.append(f"{status['exception_share_so_far']:.1%} of positions excluded or copied late "
                       "(the protocol voids the verdict above 10%)")
        keys.append("EXCEPTIONS_ABOVE_5PCT")
    for superseded in status["superseded_protocols"] or []:
        reasons.append(f"an earlier paper-ledger protocol was never finalized: {superseded}")
        keys.append("SUPERSEDED_PROTOCOL")
    sticky = list(status["sticky_attention"] or [])
    reasons += sticky
    keys += sticky
    if reasons:
        return {"check": "paper_ledger_forward", "severity": "warning",
                "summary": f"{label} needs attention: " + "; ".join(reasons), "keys": sorted(set(keys))}
    evaluation = (status["evaluation"] or {}).get("status")
    return {"check": "paper_ledger_forward", "severity": None,
            "summary": (f"{label}: " + ("registered" if status["protocol_registered"] else
                                        f"not yet registered (register by {status['register_by']})")
                        + f"; {status['positions_seen']} position(s) seen, {status['scored_positions']} scored"
                        + (f"; PAPER on and capture unpaused {share:.0%} of window sessions" if share is not None else "")
                        + f"; evaluation {evaluation}")}


def check_reviews(status: dict, today) -> dict:
    """Reviewed financials a newer filing superseded (item 53). Every due issuer is listed; it becomes a
    signature key only once its re-review is more than REVIEW_GRACE_DAYS past the filing, so the issue
    stays closed through a normal filing wave and notifies only for a lagging re-review, a review that
    no longer binds its own period, or a filing the nightly has not normalized for REVIEW_STUCK_DAYS."""
    gaps = [k for k in REVIEW_FIELDS if k not in (status or {})]
    if gaps:
        return {"check": "financial_reviews", "severity": "warning",
                "summary": f"Financial reviews: status fields missing ({', '.join(gaps)})", "keys": ["FIELDS_MISSING"]}

    def age(filed) -> int:
        return (today - date.fromisoformat(str(filed)[:10])).days if filed else 0
    due, broken = status["due"] or [], status["not_binding"] or []
    filed = status["filed_not_normalized"] or []
    overdue = [d for d in due if age(d.get("filed_at")) > REVIEW_GRACE_DAYS]
    stuck = [f for f in filed if age(f.get("filed_at")) > REVIEW_STUCK_DAYS]
    keys = sorted({f"{d['ticker']}:{d['period_end']}" for d in overdue}
                  | {f"{b['ticker']}:{b['period_end']}:NOT_BINDING" for b in broken}
                  | {f"{f['ticker']}:{f['report_date']}:NOT_NORMALIZED" for f in stuck})
    parts = []
    if due:
        parts.append(f"{len(due)} reviewed issuer(s) need a re-review after a newer filing"
                     + (f" ({len(overdue)} more than {REVIEW_GRACE_DAYS} days after filing)" if overdue else "")
                     + ": " + ", ".join(f"{d['ticker']} {d['period_end']} ({d['form']} filed {d['filed_at']})" for d in due))
    if broken:
        parts.append("review no longer binds its own period: " + ", ".join(f"{b['ticker']} {b['period_end']}"
                                                                          for b in broken))
    if filed:
        parts.append("newer filing not yet normalized: " + ", ".join(
            f"{f['ticker']} {f['form']} filed {f['filed_at']}" for f in filed))
    if status["awaiting_normalization"]:
        parts.append("re-reviewed, awaiting the nightly normalization: " + ", ".join(status["awaiting_normalization"]))
    if status.get("draft_command"):
        parts.append(f"draft with `{status['draft_command']}`")
    if status.get("custom_tickers"):
        parts.append("owner re-selects face lines (custom): " + ", ".join(status["custom_tickers"]))
    if status.get("requires"):
        parts.append("needs " + status["requires"])
    if not parts:
        return {"check": "financial_reviews", "severity": None,
                "summary": f"Financial reviews: none of {status['issuers']} reviewed issuers needs a re-review"}
    return {"check": "financial_reviews", "severity": "warning" if keys else None,
            "summary": "Financial reviews: " + "; ".join(parts), **({"keys": keys} if keys else {})}


def check_benchmark_history(dates: list[str], today) -> dict:
    first = min(dates) if dates else None
    need = (today - timedelta(days=round(365.25 * BENCHMARK_MIN_YEARS))).isoformat()
    ok = bool(first) and first <= need
    return {"check": "benchmark_history", "severity": None if ok else "warning",
            "summary": (f"{BENCHMARK} price history from {first} ({len(dates)} sessions)" if ok else
                        f"{BENCHMARK} price history starts {first or 'nowhere'}, later than {need}: replays and "
                        "volatility features need the full history (run Price History Repair)")}


def forward_checks(learning: dict, today) -> list[dict]:
    tests = (learning or {}).get("forward_tests") or {}
    return [check_forward(check, tests.get(source), today) for check, source in FORWARD_SOURCE.items()]


def overall(checks: list[dict]) -> str:
    severities = {c["severity"] for c in checks}
    return "CRITICAL" if "critical" in severities else ("WARNING" if "warning" in severities else "OK")


def markdown(checks: list[dict], status: str, now: datetime) -> str:
    icon = {"critical": "🔴", "warning": "🟡", None: "🟢"}
    lines = [f"**Health check {now.strftime('%Y-%m-%d %H:%M UTC')}: {status}**", ""]
    for c in sorted(checks, key=lambda c: {"critical": 0, "warning": 1, None: 2}[c["severity"]]):
        link = f" ([run]({c['url']}))" if c.get("url") else ""
        lines.append(f"- {icon[c['severity']]} {c['summary']}{link}")
    lines += ["", "_Automated by `health-watchdog.yml`; this issue closes itself when all checks pass._",
              f"<!-- watchdog-signature: {signature(checks)} -->"]
    return "\n".join(lines)


def signature(checks: list[dict]) -> str:
    """Which checks fail and how badly; unchanged signature means nothing new to report."""
    return ",".join(sorted(f"{c['check']}={c['severity']}" + (f"[{'+'.join(c['keys'])}]" if c.get("keys") else "")
                           for c in checks if c["severity"])) or "ok"


def _get(path: str, params: dict | None = None) -> dict:
    import httpx

    response = httpx.get(BASE_URL + path, params=params, timeout=40)
    response.raise_for_status()
    return response.json()


def _workflow_runs(file: str) -> list[dict]:
    repo = os.environ["GITHUB_REPOSITORY"]
    out = subprocess.run(
        ["gh", "api", f"repos/{repo}/actions/workflows/{file}/runs?per_page=10&exclude_pull_requests=true&branch=main",
         "--jq", "[.workflow_runs[] | {conclusion, created_at, html_url}]"],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def collect(now: datetime) -> list[dict]:
    checks = []
    try:
        health = _get("/api/v1/data-health")
        checks.append(check_prices(health))
        session = health.get("expected_market_date")
    except Exception as exc:
        checks.append({"check": "prices", "severity": "critical",
                       "summary": f"Data-health endpoint unreachable: {type(exc).__name__}"})
        session = None
    for file, severity in WORKFLOWS:
        try:
            checks.append(check_workflow(file, severity, _workflow_runs(file), now=now))
        except Exception as exc:
            checks.append({"check": file, "severity": "warning",
                           "summary": f"{file}: could not read runs ({type(exc).__name__})"})
    try:
        rows = _get(f"/api/prices/{BENCHMARK}", {"days": 5000})
        checks.append(check_benchmark_history([str(r["date"])[:10] for r in rows], now.date()))
    except Exception as exc:
        checks.append({"check": "benchmark_history", "severity": "warning",
                       "summary": f"{BENCHMARK} price history unreachable: {type(exc).__name__}"})
    try:
        learning = _get("/api/v1/agent/learning-status")
        checks += forward_checks(learning, now.date())
        checks.append(check_paper_ledger(((learning or {}).get("forward_tests") or {}).get("paper_ledger")))
    except Exception as exc:
        checks += [{"check": c, "severity": "warning", "summary": f"Forward-tracker status unreachable: {type(exc).__name__}"}
                   for c in (*FORWARD_LABEL, "paper_ledger_forward")]
    try:
        checks.append(check_reviews(_get("/api/v1/financial-reviews"), now.date()))
    except Exception as exc:
        checks.append({"check": "financial_reviews", "severity": "warning",
                       "summary": f"Financial-review status unreachable: {type(exc).__name__}"})
    if session:
        try:
            decisions = []
            for day in (now.date(), now.date() - timedelta(days=1)):
                decisions += _get("/api/agent-lab", {"day": day.isoformat(), "limit": 100})["decisions"]
            checks.append(check_coverage(decisions, session))
        except Exception as exc:
            checks.append({"check": "research_coverage", "severity": "warning",
                           "summary": f"Research journal unreachable: {type(exc).__name__}"})
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True)
    parser.add_argument("--markdown", required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    checks = collect(now)
    status = overall(checks)
    with open(args.json, "w") as f:
        json.dump({"status": status, "checked_at": now.isoformat(), "checks": checks}, f, indent=1)
    with open(args.markdown, "w") as f:
        f.write(markdown(checks, status, now))
    print(json.dumps({"status": status, "checks": [c["summary"] for c in checks]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
