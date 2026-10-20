"""Daily health watchdog: checks that prices, nightly jobs, research coverage and the two
pre-registered forward trackers (fair value, event strategies) are sound.

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
from datetime import datetime, timedelta, timezone

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
)
# Journal blockers meaning "financial data unverified": still learnable (#120).
FUNDAMENTAL_BLOCKERS = {"FINANCIAL_DEPENDENCIES_UNVERIFIED", "REQUIRED_DATA_NOT_VERIFIED", "DATA_QUALITY_NOT_PASS"}
# Without this a single workflow that stopped being scheduled (disabled, or its trigger stopped
# firing) stays green forever; every watched schedule has a run under 18h old at 13:15 UTC
# Tue-Sat. (A repository-wide 60-day inactivity disable also stops this watchdog itself.)
MAX_RUN_AGE = timedelta(hours=36)
# Forward trackers, read from GET /api/v1/agent/learning-status (the watchdog has no database).
MAX_ISOLATED_GAP_TICKERS = 3  # strategy_forward's rule: a partial session this small needs no action
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
        checks += forward_checks(_get("/api/v1/agent/learning-status"), now.date())
    except Exception as exc:
        checks += [{"check": c, "severity": "warning", "summary": f"Forward-tracker status unreachable: {type(exc).__name__}"}
                   for c in FORWARD_LABEL]
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
