"""Pure activation checks with isolated fixtures; never production writes."""
import ast
import copy
import json
from pathlib import Path

import httpx
import pytest

from scripts import activate_agent_pilot as activate
from scripts.release_agent_lab_0019 import ReleaseError
from stock_machine.agents.contracts import Policy


def test_reviewed_baseline_rejects_runtime_changes():
    activate.verify_scope(activate.ACTIVATION_FILES)
    activate.verify_scope([])
    with pytest.raises(ReleaseError, match="RUNTIME_CHANGED"):
        activate.verify_scope(["stock_machine/agents/api.py"])
    with pytest.raises(ReleaseError, match="RUNTIME_CHANGED"):
        activate.verify_scope(["scripts/release_agent_lab_0019.py"])


def cycle():
    result = {"ticker": "AAPL", "request_key": activate.CYCLE_KEY, "order_submission": False,
              "status": "COMPLETED_WITH_WITHHELD_OUTPUTS", "research_contract": {
                  "schema_version": "research-contract.v1", "snapshot_id": "test-fixture"},
              "provider_status": "UNAVAILABLE", "news_status": "UNAVAILABLE", "claims_verified": 0,
              "replayed": False}
    return result, {**copy.deepcopy(result), "replayed": True}


def test_withheld_cycle_is_not_fabricated_as_provider_success():
    row = activate.verify_cycle("AAPL", *cycle())
    assert row["provider_status"] == "UNAVAILABLE"


@pytest.mark.parametrize("field,value", [("ticker", "MSFT"), ("request_key", "wrong"),
    ("order_submission", True), ("status", "BUSY")])
def test_bad_cycle_is_not_success(field, value):
    a, b = cycle()
    a[field] = b[field] = value
    with pytest.raises(ReleaseError):
        activate.verify_cycle("AAPL", a, b)


def test_changed_cycle_retry_is_rejected():
    a, b = cycle()
    b["claims_verified"] = 1
    with pytest.raises(ReleaseError, match="REPLAY"):
        activate.verify_cycle("AAPL", a, b)


def test_mcp_tool_error_and_wrong_envelope_fail_closed():
    for result in ({"isError": True}, {"structuredContent": {"not_data": {}}}, {"content": []}):
        with pytest.raises(ReleaseError):
            activate.tool_payload(result)


def test_both_mcp_payload_encodings_are_supported():
    envelope = {"data": {"status": "OK"}}
    assert activate.tool_payload({"structuredContent": envelope}) == envelope["data"]
    assert activate.tool_payload({"content": [{"type": "text", "text": json.dumps(envelope)}]}) == envelope["data"]


def test_live_protocol_proof_checks_readonly_tools_and_actual_decisions():
    seen = []
    row = {"decision_id": "isolated-test-id"}
    def handle(req):
        assert req.url.path == "/mcp/"
        assert "authorization" not in req.headers
        body = json.loads(req.content)
        seen.append(body["method"])
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fixture"}}
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": n, "annotations": {"readOnlyHint": True, "destructiveHint": False}} for n in activate.TOOLS]}
        else:
            name = body["params"]["name"]
            data = {"status": "OK", "capture_enabled": True, "policy": Policy().model_dump(mode="json"),
                    "execution": {"status": "NOT_ENABLED"}} if name == "get_agent_status" else {"decisions": [row]}
            result = {"structuredContent": {"data": data}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
    with httpx.Client(base_url="https://fixture.invalid", transport=httpx.MockTransport(handle)) as app:
        proof = activate.mcp_proof(app, [row])
    assert proof["read_only"] and proof["journal_decisions_verified"] == 1
    assert seen == ["initialize", "notifications/initialized", "tools/list", "tools/call", "tools/call"]


def test_workflow_has_no_schedule_provider_secret_or_deployment_credential():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/agent-pilot-activation.yml").read_text()
    assert "environment: Production" in workflow
    assert "schedule:" not in workflow
    assert "MASSIVE_API" not in workflow and "VERCEL_TOKEN" not in workflow
    source = (root / "scripts/activate_agent_pilot.py").read_text()
    assert "SET TRANSACTION READ ONLY" in source
    # Reading alembic_version is required evidence, not running a migration.
    # Inspect imports and calls rather than rejecting a substring in a SELECT.
    syntax = ast.parse(source)
    forbidden = {"migrate", "upgrade", "downgrade", "enable_capture"}
    for node in ast.walk(syntax):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "alembic" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "alembic"
            assert not forbidden.intersection(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            called = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            assert called not in forbidden
