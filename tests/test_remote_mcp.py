"""Protocol and route-boundary tests. Fixtures never enter runtime storage."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient

from stock_machine.mcp_server.api_gateway import APIReadError, ResearchAPI, symbol
from stock_machine.mcp_server.http import MAX_REQUEST_BYTES, ResearchMCP

HEADERS = {"Accept": "application/json, text/event-stream"}
TOOLS = {"get_agent_status", "get_stock_research", "get_saved_analysis", "get_forecast",
         "get_data_freshness", "list_agent_decisions", "get_agent_decision", "get_price_history"}


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    app = FastAPI()
    calls = []
    report = {"ticker": "AAPL", "as_of": "2026-08-08T13:52:36Z", "analysis_id": "test-only",
              "investment_thesis": {"summary": "Test fixture, not investment research."},
              "forecasts": {}, "scenarios": [], "adversarial_review": {}, "conclusion": {}}

    @app.get("/api/agent-lab")
    async def state(request: Request):
        calls.append(("GET", request.url.path, dict(request.query_params), dict(request.headers)))
        return {"status": "OK", "capture_enabled": False, "counts": {"total": 0},
                "agents": [], "policy": {"mode": "RESEARCH", "order_submission": False},
                "execution": {"status": "NOT_ENABLED", "pnl": None, "reward": None}}

    @app.get("/api/report/{ticker}")
    async def analysis(ticker: str, request: Request):
        calls.append(("GET", request.url.path, {}, dict(request.headers)))
        return JSONResponse(report) if ticker == "AAPL" else JSONResponse({"detail": "missing"}, 404)

    @app.get("/api/v1/stocks/{ticker}/research")
    async def research(ticker: str, request: Request):
        calls.append(("GET", request.url.path, dict(request.query_params), dict(request.headers)))
        return {"ticker": ticker, "generated_at": "2026-09-16T16:00:00Z",
                "market_snapshot": {"price_date": "2026-09-15"},
                "analysis": {"report_available": True, **{k: v for k, v in report.items() if k not in {"ticker", "as_of", "analysis_id"}}}}

    @app.get("/api/predict/{ticker}")
    async def forecast(ticker: str):
        return {"ticker": ticker, "status": "OK", "as_of": "2026-09-15",
                "forecast_distribution": {"readiness_status": "DIAGNOSTIC"},
                "methodology": {"lstm": "unavailable"},
                "validation": {"verdict": {"forecast_edge": False}, "folds": ["large test-only data"]},
                "models": {"large_graph": []}}

    @app.get("/api/data-quality")
    async def quality():
        return {"as_of": "2026-09-16", "tickers": [{"ticker": "AAPL", "readiness": {"status": "READY"}}]}

    @app.get("/api/prices/{ticker}")
    async def prices(ticker: str, request: Request):
        calls.append(("GET", request.url.path, dict(request.query_params), dict(request.headers)))
        return [{"date": "2026-09-15", "adj_close": 100.0}]

    @app.get("/api/agent-lab/decisions/{identifier}")
    async def detail(identifier: str):
        return {"decision": {"decision_id": identifier, "action": "NO_TRADE"}, "frozen_evidence": {}, "events": []}

    @app.post("/api/admin/{path:path}")
    async def forbidden(path: str):
        pytest.fail("MCP reached a write route")

    app.mount("/mcp", ResearchMCP(app))
    return app, calls, report


def rpc(client, method, arguments=None, identifier=1):
    body = {"jsonrpc": "2.0", "id": identifier, "method": method}
    if arguments is not None:
        body["params"] = arguments
    return client.post("/mcp/", json=body, headers=HEADERS)


def tool(client, name, arguments=None):
    response = rpc(client, "tools/call", {"name": name, "arguments": arguments or {}})
    assert response.status_code == 200
    return response.json()["result"]


def payload(result):
    assert not result.get("isError"), result
    return json.loads(next(part["text"] for part in result["content"] if part["type"] == "text"))


def test_real_initialize_discovery_and_read_without_parent_lifespan(harness):
    app, calls, _ = harness
    client = TestClient(app)  # deliberately do NOT start an application lifespan
    init = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "test", "version": "1"}})
    assert init.status_code == 200 and "serverInfo" in init.json()["result"]
    assert "mcp-session-id" not in init.headers
    listing = rpc(client, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in listing} == TOOLS
    assert not calls, "Handshake/discovery must not read data"
    for entry in listing:
        assert entry["annotations"]["readOnlyHint"] is True
        assert entry["annotations"]["destructiveHint"] is False
    result = payload(tool(client, "get_agent_status"))
    assert result["data"]["capture_enabled"] is False
    assert result["data"]["counts"]["total"] == 0
    assert result["data"]["execution"]["pnl"] is None


def test_concurrent_cold_requests_share_no_session_manager(harness):
    app, _, _ = harness
    def read(_):
        with TestClient(app) as client:
            return payload(tool(client, "get_agent_status"))["data"]["capture_enabled"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(read, range(6))) == [False] * 6


def test_dated_report_does_not_become_current_because_packet_was_rebuilt(harness):
    app, calls, _ = harness
    with TestClient(app) as client:
        result = payload(tool(client, "get_stock_research", {"ticker": "aapl"}))
    assert result["component_dates"]["analysis_as_of"].startswith("2026-08-08")
    assert result["component_dates"]["price_date"] == "2026-09-15"
    assert result["data"]["generated_at"].startswith("2026-09-16")
    assert calls[0][2]["include_live_quote"] == "false"
    assert all(c[0] == "GET" for c in calls)


def test_inconsistent_separate_reads_do_not_fabricate_analysis_vintage(harness):
    app, _, _ = harness
    api = ResearchAPI(app)
    async def reader(path, *args, **kwargs):
        if "/research" in path:
            return {"analysis": {"investment_thesis": {"summary": "old"}}}
        return {"investment_thesis": {"summary": "new"}, "as_of": "2026-09-16"}
    api._read = reader
    result = asyncio.run(api.stock_research("AAPL"))
    assert result["component_dates"]["status"] == "UNVERIFIED"
    assert result["component_dates"]["analysis_as_of"] is None


def test_forecast_projection_keeps_readiness_and_explicit_omissions(harness):
    with TestClient(harness[0]) as client:
        result = payload(tool(client, "get_forecast", {"ticker": "AAPL"}))
    assert result["data"]["forecast_distribution"]["readiness_status"] == "DIAGNOSTIC"
    assert result["data"]["validation_summary"]["verdict"]["forecast_edge"] is False
    assert result["data"]["methodology"]["lstm"] == "unavailable"
    assert "models" in result["omitted_fields"]


@pytest.mark.parametrize("name", ["capture_agent", "save_analysis_report", "submit_order", "refresh_ticker", "set_reward"])
def test_mutating_tools_are_absent_not_just_annotated(harness, name):
    with TestClient(harness[0]) as client:
        result = tool(client, name)
    assert result.get("isError") is True
    assert not harness[1]


@pytest.mark.parametrize("ticker", ["../admin", "AAPL?token=x", "https://evil.example", "AAPL/MSFT", "%2e%2e", "AAPL\n/", "A" * 11])
def test_path_injection_rejected_before_api_access(harness, ticker):
    with TestClient(harness[0]) as client:
        assert tool(client, "get_stock_research", {"ticker": ticker})["isError"]
    assert not harness[1]


@pytest.mark.parametrize("days", [0, -1, 501, True, "500"])
def test_price_window_is_strictly_bounded(harness, days):
    with TestClient(harness[0]) as client:
        assert tool(client, "get_price_history", {"ticker": "AAPL", "days": days})["isError"]
    assert not harness[1]


def test_remaining_reads_and_missing_report(harness):
    with TestClient(harness[0]) as client:
        assert payload(tool(client, "get_saved_analysis", {"ticker": "MSFT"}))["availability"] == "MISSING"
        assert payload(tool(client, "get_data_freshness", {"ticker": "AAPL"}))["data"]["analysis_as_of"].startswith("2026-08-08")
        assert payload(tool(client, "get_price_history", {"ticker": "AAPL", "days": 2}))["data"][0]["date"] == "2026-09-15"
        assert payload(tool(client, "get_agent_decision", {"decision_id": str(uuid4())}))["data"]["decision"]["action"] == "NO_TRADE"
        assert payload(tool(client, "list_agent_decisions", {"day": "2026-09-16", "limit": 5}))["data"]["counts"]["total"] == 0
        assert tool(client, "list_agent_decisions", {"day": "2026-99-99"})["isError"]
        assert tool(client, "get_agent_decision", {"decision_id": "not-a-uuid"})["isError"]


@pytest.mark.parametrize("code", [401, 403, 404, 429, 500, 503])
def test_backend_errors_are_not_empty_success_or_secret_leaks(code, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    app = FastAPI()
    @app.get("/api/agent-lab")
    def error():
        return JSONResponse({"detail": "password=SENSITIVE_VALUE"}, code)
    app.mount("/mcp", ResearchMCP(app))
    with TestClient(app) as client:
        result = tool(client, "get_agent_status")
    assert result["isError"] is True
    assert "SENSITIVE_VALUE" not in json.dumps(result)
    assert f"API_READ_HTTP_{code}" in json.dumps(result)


def test_redirects_and_nonfinite_response_are_not_followed_or_accepted():
    app = FastAPI()
    @app.get("/api/agent-lab")
    def redirect():
        return PlainTextResponse("secret", 302, headers={"Location": "https://evil.example"})
    @app.get("/api/predict/AAPL")
    def bad():
        return PlainTextResponse('{"value": NaN}', media_type="application/json")
    api = ResearchAPI(app)
    with pytest.raises(APIReadError, match="HTTP_302"):
        asyncio.run(api.agent_status())
    with pytest.raises(APIReadError, match="UNAVAILABLE"):
        asyncio.run(api.forecast("AAPL"))
    with pytest.raises(APIReadError, match="ROUTE_NOT_ALLOWED"):
        asyncio.run(api._read("/api/admin/jobs"))


def test_incoming_tokens_and_cookies_are_not_forwarded(harness):
    with TestClient(harness[0]) as client:
        client.headers.update({"Authorization": "Bearer CLIENT_SECRET", "Cookie": "private=COOKIE_SECRET"})
        payload(tool(client, "get_agent_status"))
    assert "CLIENT_SECRET" not in str(harness[1])
    assert "COOKIE_SECRET" not in str(harness[1])


def test_body_limit_and_transport_methods(harness):
    with TestClient(harness[0]) as client:
        assert client.post("/mcp/", content=b"x" * (MAX_REQUEST_BYTES + 1), headers=HEADERS).status_code == 413
        for method in ("GET", "DELETE", "PUT"):
            assert client.request(method, "/mcp/").status_code == 405
    assert not harness[1]


def test_dns_rebinding_and_foreign_origin_rejected(harness):
    with TestClient(harness[0]) as client:
        client.headers["Host"] = "evil.example"
        assert rpc(client, "tools/list").status_code in {403, 421}
        client.headers["Host"] = "testserver"
        client.headers["Origin"] = "https://evil.example"
        assert rpc(client, "tools/list").status_code in {403, 421}
    assert not harness[1]


def test_actual_production_app_mount_discovery_has_no_database_side_effect(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    from stock_machine import db
    from stock_machine.webapp_automation import app
    def forbidden(*args, **kwargs):
        pytest.fail("Discovery accessed the database")
    monkeypatch.setattr(db, "connect", forbidden)
    with TestClient(app) as client:
        result = rpc(client, "tools/list")
    assert result.status_code == 200
    assert {t["name"] for t in result.json()["result"]["tools"]} == TOOLS
