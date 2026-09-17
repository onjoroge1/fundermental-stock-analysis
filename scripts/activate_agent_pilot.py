"""Resume explicitly enabled research on the reviewed post-PR62 runtime.

No migration, environment edit, deployment API, broker call or recurring job.
All writes use existing authenticated research APIs. Database checks are read
only. Provider credentials stay on Vercel; at most two reads per fresh cycle.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID

import httpx

from scripts.release_agent_lab_0019 import (
    HOST, PILOT, INITIAL_KEY, ReleaseError, require, request, git,
    current_main, verify_record, verify_state,
)
from scripts.release_research_integrity_0020 import preflight

REVIEWED_BASE = "c3e93bb6be70388fe8604cfaa2d4b9955382f04e"
SCHEMA = "0021_monitoring_storage"
CYCLE_KEY = "agent-pilot-activation-v1"
ACTIVATION_FILES = {
    ".github/workflows/agent-pilot-activation.yml",
    "scripts/activate_agent_pilot.py",
    "tests/test_agent_pilot_activation.py",
    "docs/AGENT_PILOT_ACTIVATION.md",
}
TABLES = ("agent_lab_policies", "agent_lab_evidence", "agent_lab_decisions",
          "agent_lab_events", "agent_lab_report_outbox")
TOOLS = {"get_agent_status", "get_stock_research", "get_saved_analysis", "get_forecast",
         "get_data_freshness", "list_agent_decisions", "get_agent_decision", "get_price_history"}


def verify_scope(changed):
    require(set(changed) <= ACTIVATION_FILES, "RUNTIME_CHANGED_SINCE_REVIEWED_ACTIVATION_BASE")


def database_proof(rows=()):
    from stock_machine import db
    require(db.REQUIRED_SCHEMA_VERSION == SCHEMA, "SCHEMA_DECLARATION_CHANGED")
    with db.connect() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        versions = {r[0] for r in conn.execute("SELECT version_num FROM alembic_version").fetchall()}
        require(versions == {SCHEMA}, "DATABASE_REVISION_NOT_REVIEWED")
        for table in TABLES:
            require(conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is not None, "JOURNAL_TABLE_MISSING")
            count = conn.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal AND tgenabled='O'", (table,)).fetchone()[0]
            require(count == 2, "JOURNAL_AUDIT_PROTECTION_MISSING")
        for row in rows:
            proof = conn.execute("""SELECT d.input_sha256, count(e.event_id), count(o.event_id)
                FROM agent_lab_decisions d
                LEFT JOIN agent_lab_events e ON e.decision_id=d.decision_id AND e.event_type='RECORDED'
                LEFT JOIN agent_lab_report_outbox o ON o.event_id=e.event_id
                WHERE d.decision_id=%s AND d.request_key=%s GROUP BY d.input_sha256""",
                (row["decision_id"], INITIAL_KEY)).fetchone()
            require(proof == (row["input_sha256"], 1, 1), "INITIAL_EVENT_OUTBOX_PROOF_FAILED")
    return {"schema": SCHEMA, "audit_triggers": 10, "verified_decisions": len(rows), "read_only": True}


def verify_cycle(ticker, initial, replay):
    require(initial.get("ticker") == ticker and initial.get("request_key") == CYCLE_KEY, "CYCLE_IDENTITY_MISMATCH")
    require(initial.get("order_submission") is False, "CYCLE_ORDER_BOUNDARY_VIOLATION")
    require(initial.get("status") in {"COMPLETED", "COMPLETED_WITH_WITHHELD_OUTPUTS"}, "CYCLE_NOT_COMPLETED")
    require(replay.get("replayed") is True and
            {k: v for k, v in initial.items() if k != "replayed"} ==
            {k: v for k, v in replay.items() if k != "replayed"}, "CYCLE_REPLAY_MISMATCH")
    contract = initial.get("research_contract") or {}
    require(contract.get("schema_version") == "research-contract.v1" and bool(contract.get("snapshot_id")), "CYCLE_DATED_CONTRACT_MISSING")
    return {key: initial.get(key) for key in ("ticker", "status", "report_id", "completed_at",
                                             "provider_status", "news_status", "claims_verified")}


def verify_capture(ticker, initial, detail, replay):
    row = verify_record(ticker, initial, detail, replay)
    contract = detail["frozen_evidence"].get("research_contract") or {}
    decision = initial["decision"]
    require(contract.get("schema_version") == "research-contract.v1", "JOURNAL_CONTRACT_MISSING")
    require(bool(contract.get("snapshot_id")) and contract["snapshot_id"] == decision.get("research_snapshot_id"), "JOURNAL_SNAPSHOT_MISMATCH")
    require(contract.get("report_id") == decision.get("source_report_id") and
            contract.get("report_as_of") == decision.get("source_report_as_of"), "JOURNAL_REPORT_PROVENANCE_MISMATCH")
    row.update({key: decision.get(key) for key in ("source_report_id", "source_report_as_of", "research_snapshot_id")})
    return row


def rpc(app, identifier, method, params, version="2025-06-18"):
    value = request(app, "POST", "/mcp/", headers={
        "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": version},
        json={"jsonrpc": "2.0", "id": identifier, "method": method, "params": params})
    require(value.get("jsonrpc") == "2.0" and value.get("id") == identifier and
            isinstance(value.get("result"), dict) and "error" not in value, "MCP_PROTOCOL_ERROR")
    return value["result"]


def tool_payload(result):
    require(not result.get("isError"), "MCP_TOOL_READ_FAILED")
    content = result.get("structuredContent")
    if not isinstance(content, dict):
        parts = [part.get("text") for part in result.get("content", []) if part.get("type") == "text"]
        require(len(parts) == 1, "MCP_TOOL_PAYLOAD_MISSING")
        content = json.loads(parts[0])
    require(isinstance(content, dict) and isinstance(content.get("data"), dict), "MCP_TOOL_ENVELOPE_INVALID")
    return content["data"]


def mcp_proof(app, rows):
    # This client has no default Authorization header. Admin credentials are
    # attached to the explicit research writes only, never passed through MCP.
    initialized = rpc(app, 1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                      "clientInfo": {"name": "stock-machine-activation-verifier", "version": "1"}})
    version = initialized.get("protocolVersion")
    require(bool(version) and isinstance(initialized.get("serverInfo"), dict), "MCP_INITIALIZATION_FAILED")
    notified = app.post("/mcp/", headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": version},
                       json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    require(notified.status_code in {202, 204}, "MCP_INITIALIZED_NOTIFICATION_FAILED")
    listing = rpc(app, 2, "tools/list", {}, version)
    entries = listing.get("tools") or []
    require(len(entries) == len(TOOLS) and {entry["name"] for entry in entries} == TOOLS
            and not listing.get("nextCursor"), "MCP_TOOL_ALLOWLIST_MISMATCH")
    require(all(entry.get("annotations", {}).get("readOnlyHint") is True and
                entry.get("annotations", {}).get("destructiveHint") is False for entry in entries), "MCP_WRITE_CAPABILITY_EXPOSED")
    state = tool_payload(rpc(app, 3, "tools/call", {"name": "get_agent_status", "arguments": {}}, version))
    verify_state(state)
    require(state.get("capture_enabled") is True, "MCP_CAPTURE_FLAG_NOT_OBSERVED")
    journal = tool_payload(rpc(app, 4, "tools/call", {"name": "list_agent_decisions", "arguments": {"limit": 25}}, version))
    ids = {d["decision_id"] for d in journal.get("decisions", [])}
    require({row["decision_id"] for row in rows} <= ids, "MCP_JOURNAL_READ_MISMATCH")
    return {"status": "VERIFIED", "protocol_version": version, "tool_count": len(entries),
            "read_only": True, "journal_decisions_verified": len(rows), "authenticated_admin_token_forwarded": False}


def run(report):
    sha = os.getenv("GITHUB_SHA", "")
    report.update(status="STARTED", commit=sha, reviewed_base=REVIEWED_BASE,
                  cycles=[], captures=[], trade_execution=False)
    report["credential_presence"] = {name: bool(os.getenv(name)) for name in ("DATABASE_URL", "STOCK_MACHINE_ADMIN_TOKEN", "GH_TOKEN")}
    token = os.getenv("STOCK_MACHINE_ADMIN_TOKEN", "")
    require(len(token) >= 24, "ADMIN_TOKEN_NOT_CONFIGURED")
    require(bool(os.getenv("GH_TOKEN")), "GITHUB_READ_TOKEN_MISSING")
    require(bool(os.getenv("DATABASE_URL")), "DATABASE_URL_NOT_CONFIGURED")
    git("merge-base", "--is-ancestor", REVIEWED_BASE, sha)
    verify_scope(git("diff", "--name-only", REVIEWED_BASE, sha).splitlines())
    with httpx.Client(base_url="https://api.github.com", headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"]},
                      timeout=30, follow_redirects=False) as gh:
        preflight(gh, sha)
        report["preflight"] = "MATCHING_MAIN_CI_BUILD_AND_REVIEWED_RUNTIME"
        report["database_before"] = database_proof()
        with httpx.Client(base_url="https://" + HOST, timeout=310,
                          headers={"Cache-Control": "no-cache"}, follow_redirects=False) as app:
            before = verify_state(request(app, "GET", "/api/agent-lab"))
            require(before.get("capture_enabled") is True, "PRODUCTION_CAPTURE_NOT_ENABLED")
            report["journal_before"] = before["counts"]
            for ticker in PILOT:
                current_main(gh, sha)
                path = f"/api/admin/research/{ticker}/run"
                kwargs = {"headers": {"Authorization": "Bearer " + token}, "json": {"idempotency_key": CYCLE_KEY}}
                original = request(app, "POST", path, **kwargs)
                repeated = request(app, "POST", path, **kwargs)
                report["cycles"].append(verify_cycle(ticker, original, repeated))
                print(f"{ticker}: source cycle and identical retry verified", flush=True)
            for ticker in PILOT:
                current_main(gh, sha)
                path = f"/api/admin/agents/{ticker}/capture"
                kwargs = {"headers": {"Authorization": "Bearer " + token}, "json": {"idempotency_key": INITIAL_KEY}}
                original = request(app, "POST", path, **kwargs)
                identifier = str(UUID(original["decision"]["decision_id"]))
                detail = request(app, "GET", "/api/agent-lab/decisions/" + identifier)
                repeated = request(app, "POST", path, **kwargs)
                row = verify_capture(ticker, original, detail, repeated)
                report["captures"].append(row)
                print(f"{ticker}: {row['status']} {identifier}; saved evidence and replay verified", flush=True)
            require(len(report["captures"]) == 5 and len({r["decision_id"] for r in report["captures"]}) == 5,
                    "FIVE_UNIQUE_CAPTURES_NOT_VERIFIED")
            require(all(r["status"] != "FAILED" for r in report["captures"]), "RESEARCH_CAPTURE_FAILED")
            report["database_after"] = database_proof(report["captures"])
            report["journal_after"] = verify_state(request(app, "GET", "/api/agent-lab"))["counts"]
            report["mcp"] = mcp_proof(app, report["captures"])
    providers_ok = all(c["provider_status"] == "OBSERVED" and c["news_status"] in {"OBSERVED", "NO_ARTICLES_RETURNED"}
                       for c in report["cycles"])
    report["status"] = "VERIFIED" if providers_ok else "CAPTURES_VERIFIED_PROVIDER_INCOMPLETE"


def main():
    report = {}
    try:
        run(report)
    except ReleaseError as exc:
        report.update(status="BLOCKED", error_code=str(exc))
    except Exception as exc:
        report.update(status="BLOCKED", error_code="UNEXPECTED_" + type(exc).__name__)
    text = json.dumps(report, indent=2) + "\n"
    Path("agent-pilot-activation-report.json").write_text(text)
    print(text, flush=True)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as handle:
            handle.write("## Agent pilot activation\n\n```json\n" + text + "```\n")
    return 0 if report.get("status") in {"VERIFIED", "CAPTURES_VERIFIED_PROVIDER_INCOMPLETE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
