"""Current-main evidence release (0020 + read-only storage fix 0021); no orders."""
import json
import os
from pathlib import Path
import time

import httpx

from scripts.release_agent_lab_0019 import (REPO, HOST, PILOT, ReleaseError, require,
    request, current_main, enable_capture, verify_record, verify_state)


def preflight(gh, sha):
    require(os.getenv("GITHUB_REPOSITORY") == REPO, "WRONG_REPOSITORY")
    require(os.getenv("GITHUB_REF") == "refs/heads/main", "MAIN_ONLY")
    require(len(sha) == 40, "COMMIT_ID_MISSING")
    for _ in range(60):
        current_main(gh, sha)
        runs = request(gh, "GET", f"/repos/{REPO}/actions/runs", params={"head_sha": sha, "event": "push", "per_page": 100})["workflow_runs"]
        ci = [r for r in runs if r["path"] == ".github/workflows/ci.yml"]
        if ci and ci[0]["status"] == "completed":
            require(ci[0]["conclusion"] == "success", "MATCHING_CI_NOT_SUCCESSFUL")
            statuses = request(gh, "GET", f"/repos/{REPO}/commits/{sha}/status")["statuses"]
            vc = [s for s in statuses if s["context"] == "Vercel"]
            if vc and vc[0]["state"] == "success":
                break
        time.sleep(10)
    else:
        raise ReleaseError("MATCHING_CI_OR_DEPLOYMENT_NOT_READY")
    # Preserve the earlier release's protection against old active writers.
    for state in ("in_progress", "queued", "waiting", "pending", "requested"):
        for page in range(1, 6):
            runs = request(gh, "GET", f"/repos/{REPO}/actions/runs", params={"status": state, "per_page": 100, "page": page})["workflow_runs"]
            for row in runs:
                if str(row["id"]) == os.getenv("GITHUB_RUN_ID") or row["path"] == ".github/workflows/ci.yml":
                    continue
                require(row["head_sha"] == sha, "OTHER_WORKER_ACTIVE_REVIEW_REQUIRED")
            if len(runs) < 100:
                break
        else:
            raise ReleaseError("ACTIVE_WORKER_LIST_INCOMPLETE")
    current_main(gh, sha)


def run(report):
    sha = os.getenv("GITHUB_SHA", "")
    report.update({"status": "STARTED", "commit": sha, "trade_execution": False, "captures": []})
    require(bool(os.getenv("GH_TOKEN")), "GITHUB_READ_TOKEN_MISSING")
    with httpx.Client(base_url="https://api.github.com", headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"]}, timeout=30) as gh:
        preflight(gh, sha)
        report["preflight"] = "CURRENT_MAIN_CI_AND_DEPLOYMENT_VERIFIED"
        from scripts.research_integrity_migration import migrate
        report["migration"] = migrate()
        from scripts.run_research_operations import reconcile
        report["reconciliation"] = reconcile(refresh_sec=True)
        require(report["reconciliation"]["status"] == "AUDITED", "RECONCILIATION_INCOMPLETE")
        from stock_machine.research_cycle import run as cycle
        report["cycles"] = [cycle(t, "release-0020-source-brief", collect_market_data=False) for t in PILOT]
        from scripts.build_coverage_snapshot import main as index
        require(index() == 0, "INDEX_BUILD_FAILED")
        from stock_machine import db
        from stock_machine.control_plane import coverage_rows
        with db.connect() as conn:
            published = coverage_rows(conn)
        require(len(published) == report["reconciliation"]["expected"] and
                all(row["index_status"] == "READY" for row in published),
                "DURABLE_COVERAGE_INDEX_INCOMPLETE")
        report["index"] = "ALL_CONFIGURED_NAMES_PUBLISHED"
        from stock_machine.prospective_experiment import run as experiment
        report["experiment"] = experiment()
        require(not report["experiment"].get("status", "").startswith(("BLOCKED", "INVALID")), "EXPERIMENT_INPUTS_BLOCKED")
        current_main(gh, sha)
        token = os.getenv("STOCK_MACHINE_ADMIN_TOKEN", "")
        require(len(token) >= 24, "ADMIN_TOKEN_NOT_CONFIGURED_PRODUCTION_INTEGRATION_UNEXERCISED")
        with httpx.Client(base_url="https://" + HOST, timeout=310, headers={"Cache-Control": "no-cache"}, follow_redirects=False) as app:
            kwargs = {"headers": {"Authorization": "Bearer " + token}, "json": {"idempotency_key": "release-0020-production-provider"}}
            report["production_integration"] = request(app, "POST", "/api/admin/research/VZ/run", **kwargs)
            require(report["production_integration"].get("provider_status") == "OBSERVED", "MASSIVE_PRODUCTION_DAILY_OBSERVATION_FAILED")
            enable_capture(app, gh, sha, report)
            for ticker in PILOT:
                path = f"/api/admin/agents/{ticker}/capture"
                kwargs["json"] = {"idempotency_key": "research-integrity-0020-first-capture"}
                initial = request(app, "POST", path, **kwargs)
                detail = request(app, "GET", "/api/agent-lab/decisions/" + initial["decision"]["decision_id"])
                replay = request(app, "POST", path, **kwargs)
                row = verify_record(ticker, initial, detail, replay)
                contract = detail["frozen_evidence"].get("research_contract") or {}
                require(contract.get("schema_version") == "research-contract.v1", "JOURNAL_CONTRACT_MISSING")
                require(contract.get("snapshot_id") == initial["decision"].get("research_snapshot_id"), "JOURNAL_SNAPSHOT_MISMATCH")
                report["captures"].append(row)
            report["journal"] = verify_state(request(app, "GET", "/api/agent-lab"))["counts"]
            require(all(row["status"] != "FAILED" for row in report["captures"]), "RESEARCH_CAPTURE_FAILED")
        report["status"] = "VERIFIED"


def main():
    report = {}
    try:
        run(report)
    except ReleaseError as exc:
        report.update(status="BLOCKED", error_code=str(exc))
    except Exception as exc:
        report.update(status="BLOCKED", error_code="UNEXPECTED_" + type(exc).__name__)
    Path("research-integrity-release-report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({k: report[k] for k in ("status", "commit", "error_code", "index") if k in report}))
    return 0 if report.get("status") == "VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
