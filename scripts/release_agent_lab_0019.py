"""Bounded, production-only release of PR54. Never submits a broker order.

The runner uses existing secrets in the authorized Production environment.
It never exports them. A missing Vercel token stops enablement, not the already
verified database migration. Re-runs retain the fixed initial capture key.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import UUID

import httpx

REPO = "onjoroge1/fundermental-stock-analysis"
BASE = "0e7e9970d8b1d0777068b1129335ccb1bedd024e"
TARGET = "0019_agent_lab"
HOST = "fundermental-stock-analysis.vercel.app"
PROJECT = "prj_6QqP0HB8rbV1Hr1xFH0VVx5BD8ed"
TEAM = "team_eql8ciDAOWzLf2pAe4Gd4WD4"
PILOT = ("AAPL", "MSFT", "UBER", "HIMS", "VZ")
INITIAL_KEY = "agent-pilot-initial-001"
TABLES = ("agent_lab_policies", "agent_lab_evidence", "agent_lab_decisions", "agent_lab_events", "agent_lab_report_outbox")
RELEASE_FILES = {
    ".github/workflows/agent-lab-release-0019.yml",
    "scripts/release_agent_lab_0019.py",
    "tests/test_agent_lab_release_runner.py",
    "docs/AGENT_LAB_RELEASE_0019.md",
}


class ReleaseError(RuntimeError):
    """Codes are fixed strings. Never put a provider response or secret here."""


def require(condition, code):
    if not condition:
        raise ReleaseError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    require(200 <= response.status_code < 300, "HTTP_" + str(response.status_code))
    return response.json()


def git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=30)
    require(result.returncode == 0, "GIT_READ_CHECK_FAILED")
    return result.stdout.strip()


def current_main(gh, sha):
    info = request(gh, "GET", f"/repos/{REPO}/branches/main")
    require(info["commit"]["sha"] == sha, "MAIN_MOVED_REVIEW_REQUIRED")


def preflight(gh, sha):
    require(os.getenv("GITHUB_REPOSITORY") == REPO, "WRONG_REPOSITORY")
    require(os.getenv("GITHUB_REF") == "refs/heads/main", "MAIN_ONLY")
    require(len(sha) == 40, "COMMIT_ID_MISSING")
    git("merge-base", "--is-ancestor", BASE, sha)
    changed = set(git("diff", "--name-only", BASE, sha).splitlines())
    require(changed <= RELEASE_FILES, "RUNTIME_CHANGED_SINCE_REVIEWED_PR54")
    # Wait only for the matching main-branch CI and Git integration build.
    for _ in range(60):
        current_main(gh, sha)
        runs = request(gh, "GET", f"/repos/{REPO}/actions/runs", params={"head_sha": sha, "event": "push", "per_page": 100})["workflow_runs"]
        ci = [r for r in runs if r["path"] == ".github/workflows/ci.yml"]
        if ci and ci[0]["status"] == "completed":
            require(ci[0]["conclusion"] == "success", "MATCHING_CI_NOT_SUCCESSFUL")
            checks = request(gh, "GET", f"/repos/{REPO}/commits/{sha}/status")["statuses"]
            vercel = [s for s in checks if s["context"] == "Vercel"]
            if vercel and vercel[0]["state"] == "success":
                break
        time.sleep(10)
    else:
        raise ReleaseError("MATCHING_CI_OR_BUILD_NOT_READY")
    # Old running/queued workers cannot be migrated underneath. Do not cancel
    # unrelated jobs. Pagination is bounded and overflow fails closed.
    for state in ("in_progress", "queued", "waiting", "pending", "requested"):
        for page in range(1, 6):
            rows = request(gh, "GET", f"/repos/{REPO}/actions/runs", params={"status": state, "per_page": 100, "page": page})["workflow_runs"]
            for run in rows:
                if str(run["id"]) == os.getenv("GITHUB_RUN_ID") or run["path"] == ".github/workflows/ci.yml":
                    continue
                require(run["head_sha"] == sha, "OTHER_WORKER_ACTIVE_REVIEW_REQUIRED")
            if len(rows) < 100:
                break
        else:
            raise ReleaseError("ACTIVE_WORKER_LIST_INCOMPLETE")
    current_main(gh, sha)


def migrate():
    from stock_machine import db
    require(db.REQUIRED_SCHEMA_VERSION == TARGET, "SCHEMA_DECLARATION_MISMATCH")
    require(bool(os.getenv("DATABASE_URL")), "DATABASE_URL_NOT_CONFIGURED")
    with db.connect() as conn:
        conn.autocommit = True
        locked = conn.execute("SELECT pg_try_advisory_lock(hashtextextended('agent-lab-release-0019',0))").fetchone()[0]
        require(locked, "ANOTHER_RELEASE_HOLDS_DATABASE_LOCK")
        versions = {r[0] for r in conn.execute("SELECT version_num FROM alembic_version").fetchall()}
        require(versions in ({"0018_consensus_archives"}, {TARGET}), "UNEXPECTED_DATABASE_REVISION")
        if versions != {TARGET}:
            applied = subprocess.run(["alembic", "upgrade", TARGET], capture_output=True, text=True, timeout=180)
            require(applied.returncode == 0, "MIGRATION_FAILED_RECONCILE_BEFORE_RETRY")
        db.init_schema(conn)
        for table in TABLES:
            require(conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is not None, "JOURNAL_TABLE_MISSING")
            triggers = conn.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal AND tgenabled='O'", (table,)).fetchone()[0]
            require(triggers == 2, "AUDIT_PROTECTION_MISSING")
        return {"before": sorted(versions), "after": TARGET, "verified_tables": list(TABLES), "audit_triggers": 10}


def verify_state(data):
    require(data.get("status") == "OK", "JOURNAL_NOT_READABLE")
    policy = data.get("policy") or {}
    require(policy.get("mode") == "RESEARCH" and policy.get("policy_id") == "research-pilot-v1", "POLICY_MISMATCH")
    require(tuple(policy.get("tickers", [])) == PILOT, "PILOT_UNIVERSE_MISMATCH")
    for name in ("order_submission", "simulated_execution", "exploration_enabled", "reward_enabled", "qualified_forward_paper"):
        require(policy.get(name) is False, "UNEXPECTED_CAPABILITY_ENABLED")
    require((data.get("execution") or {}).get("status") == "NOT_ENABLED", "EXECUTION_NOT_DISABLED")
    return data


def enable_capture(app, gh, sha, report):
    current = verify_state(request(app, "GET", "/api/agent-lab"))
    if current.get("capture_enabled") is True:
        report["enablement"] = "ALREADY_ENABLED"
        return
    token = os.getenv("VERCEL_TOKEN", "")
    require(bool(token), "VERCEL_TOKEN_NOT_CONFIGURED_CAPTURE_REMAINS_DISABLED")
    current_main(gh, sha)
    with httpx.Client(base_url="https://api.vercel.com", headers={"Authorization": "Bearer " + token}, params={"teamId": TEAM}, timeout=45, follow_redirects=False) as vc:
        project = request(vc, "GET", f"/v9/projects/{PROJECT}")
        require(project.get("id") == PROJECT and project.get("accountId") == TEAM, "VERCEL_PROJECT_MISMATCH")
        created = request(vc, "POST", f"/v10/projects/{PROJECT}/env", params={"teamId": TEAM, "upsert": "true"}, json={"key": "AGENT_LAB_ENABLED", "value": "true", "type": "plain", "target": ["production"]})
        require(not created.get("failed"), "VERCEL_ENV_UPDATE_FAILED")
        report["enablement"] = "PRODUCTION_FLAG_SET_REDEPLOY_PENDING"
        # A new git deployment consumes the current project environment. Do not
        # inherit the old deployment's disabled environment via deploymentId.
        deployed = request(vc, "POST", "/v13/deployments", params={"teamId": TEAM, "forceNew": "1"}, json={"name": "fundermental-stock-analysis", "project": PROJECT, "target": "production", "gitSource": {"type": "github", "repoId": "1327796320", "ref": "main", "sha": sha}})
        deployment_id = deployed.get("id", "")
        require(deployment_id.startswith("dpl_"), "DEPLOYMENT_NOT_CONFIRMED")
        report["deployment_id"] = deployment_id
        for _ in range(90):
            data = request(vc, "GET", "/v13/deployments/" + deployment_id)
            state = data.get("readyState") or data.get("state")
            require(state not in {"ERROR", "CANCELED"}, "PRODUCTION_REDEPLOY_FAILED")
            if state == "READY":
                require((data.get("meta") or {}).get("githubCommitSha") == sha, "DEPLOYED_COMMIT_MISMATCH")
                require(data.get("target") == "production", "WRONG_DEPLOYMENT_TARGET")
                break
            time.sleep(5)
        else:
            raise ReleaseError("PRODUCTION_REDEPLOY_PENDING")
    for _ in range(30):
        current = verify_state(request(app, "GET", "/api/agent-lab"))
        if current.get("capture_enabled") is True:
            report["enablement"] = "ENABLED_AND_OBSERVED_IN_PRODUCTION"
            return
        time.sleep(5)
    raise ReleaseError("PRODUCTION_FLAG_NOT_OBSERVED")


def verify_record(ticker, initial, detail, replay):
    decision = initial["decision"]
    UUID(decision["decision_id"])
    require(detail.get("decision") == decision, "PERSISTED_DECISION_MISMATCH")
    require(replay.get("replayed") is True and replay.get("decision") == decision, "IDEMPOTENCY_VERIFICATION_FAILED")
    require(decision.get("ticker") == ticker and decision.get("policy_id") == "research-pilot-v1", "DECISION_IDENTITY_MISMATCH")
    require(decision.get("mode") == "RESEARCH" and decision.get("execution_status") == "NOT_ENABLED", "EXECUTION_BOUNDARY_VIOLATION")
    require(decision.get("reward") is None and decision.get("pnl") is None, "UNMEASURED_RESULT_WAS_INVENTED")
    require(decision.get("selection_mode") == "RULE_BASED", "UNEXPECTED_LEARNING_MODE")
    require((decision.get("status"), decision.get("action")) in {("RECORDED", "WATCH"), ("BLOCKED", "NO_TRADE"), ("FAILED", "NO_TRADE")}, "INVALID_DECISION_STATUS")
    require(bool(decision.get("rationale")), "RATIONALE_MISSING")
    require(fingerprint(detail["frozen_evidence"]) == decision.get("input_sha256"), "FROZEN_EVIDENCE_HASH_MISMATCH")
    events = detail.get("events") or []
    require(sum(e.get("event_type") == "RECORDED" for e in events) == 1, "INITIAL_EVENT_MISSING_OR_DUPLICATE")
    require(all(e.get("event_type") in {"RECORDED", "REVIEW"} for e in events), "UNEXPECTED_EXECUTION_EVENT")
    return {key: decision.get(key) for key in ("ticker", "decision_id", "status", "action", "observed_at", "price_date", "input_sha256", "blockers")}


def verify_db_records(rows):
    from stock_machine import db
    with db.connect() as conn:
        for row in rows:
            result = conn.execute("""SELECT d.input_sha256, count(e.event_id), count(o.event_id)
                FROM agent_lab_decisions d
                LEFT JOIN agent_lab_events e ON e.decision_id=d.decision_id AND e.event_type='RECORDED'
                LEFT JOIN agent_lab_report_outbox o ON o.event_id=e.event_id
                WHERE d.decision_id=%s AND d.request_key=%s
                GROUP BY d.input_sha256""", (row["decision_id"], INITIAL_KEY)).fetchone()
            require(result is not None and result == (row["input_sha256"], 1, 1), "DATABASE_EVENT_OUTBOX_VERIFICATION_FAILED")


def run(report):
    sha = os.getenv("GITHUB_SHA", "")
    report.update({"schema_target": TARGET, "commit": sha, "captures": [], "enablement": "NOT_ATTEMPTED", "status": "STARTED"})
    report["credential_presence"] = {name: bool(os.getenv(name)) for name in ("DATABASE_URL", "STOCK_MACHINE_ADMIN_TOKEN", "VERCEL_TOKEN")}
    print("Credential presence only: " + json.dumps(report["credential_presence"]), flush=True)
    require(bool(os.getenv("GH_TOKEN")), "GITHUB_READ_TOKEN_MISSING")
    with httpx.Client(base_url="https://api.github.com", headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json"}, timeout=30, follow_redirects=False) as gh:
        preflight(gh, sha)
        report["preflight"] = "MATCHING_CI_AND_BUILD_PASSED_NO_OLD_WORKERS"
        print("Preflight passed; applying exact migration 0019.", flush=True)
        report["migration"] = migrate()
        print("Database revision and ten audit triggers verified.", flush=True)
        with httpx.Client(base_url="https://" + HOST, headers={"Cache-Control": "no-cache"}, timeout=310, follow_redirects=False) as app:
            for path in ("/", "/trades", "/agents"):
                response = app.get(path)
                require(response.status_code == 200 and 'href="/agents"' in response.text, "PRODUCTION_MENU_OR_PAGE_UNAVAILABLE")
            state = verify_state(request(app, "GET", "/api/agent-lab"))
            report["journal_before"] = state.get("counts")
            report["pages"] = "ROOT_TRADES_AGENTS_RETURN_200_WITH_MENU_LINK"
            enable_capture(app, gh, sha, report)
            token = os.getenv("STOCK_MACHINE_ADMIN_TOKEN", "")
            require(len(token) >= 24, "ADMIN_TOKEN_NOT_CONFIGURED")
            current_main(gh, sha)
            for ticker in PILOT:
                path = f"/api/admin/agents/{ticker}/capture"
                kwargs = {"headers": {"Authorization": "Bearer " + token}, "json": {"idempotency_key": INITIAL_KEY}}
                initial = request(app, "POST", path, **kwargs)
                identifier = str(UUID(initial["decision"]["decision_id"]))
                detail = request(app, "GET", "/api/agent-lab/decisions/" + identifier)
                replay = request(app, "POST", path, **kwargs)
                row = verify_record(ticker, initial, detail, replay)
                report["captures"].append(row)
                print(f"{ticker}: {row['status']} {identifier}; evidence and replay verified", flush=True)
            verify_db_records(report["captures"])
            report["journal_after"] = verify_state(request(app, "GET", "/api/agent-lab")).get("counts")
            require(len(report["captures"]) == 5, "FIVE_CAPTURES_NOT_VERIFIED")
            require(all(row["status"] != "FAILED" for row in report["captures"]), "RESEARCH_SOURCE_FAILURE_REQUIRES_REVIEW")
            report["status"] = "VERIFIED"


def main():
    report = {}
    try:
        run(report)
    except ReleaseError as exc:
        report["status"] = "BLOCKED"
        report["error_code"] = str(exc)
    except Exception as exc:
        # Error class only. HTTP/provider/DB exceptions can contain secrets.
        report["status"] = "BLOCKED"
        report["error_code"] = "UNEXPECTED_" + type(exc).__name__
    Path("agent-lab-release-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as handle:
            handle.write("## Agent Lab 0019 release\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n")
    return 0 if report.get("status") == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
