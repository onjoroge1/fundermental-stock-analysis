"""Daily health watchdog: checks that prices, nightly jobs and research coverage are sound.

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


def check_workflow(name: str, severity: str, runs: list[dict]) -> dict:
    """runs: newest first, each {conclusion, created_at, html_url}. The latest
    completed run decides: a failed first attempt followed by a successful
    retry is healthy."""
    completed = [r for r in runs if r.get("conclusion")]
    if not completed:
        return {"check": name, "severity": "warning", "summary": f"{name}: no completed runs found"}
    latest = completed[0]
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
    return ",".join(sorted(f"{c['check']}={c['severity']}" for c in checks if c["severity"])) or "ok"


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
            checks.append(check_workflow(file, severity, _workflow_runs(file)))
        except Exception as exc:
            checks.append({"check": file, "severity": "warning",
                           "summary": f"{file}: could not read runs ({type(exc).__name__})"})
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
