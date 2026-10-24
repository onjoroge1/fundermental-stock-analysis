"""Every state-changing route the deployment serves must authenticate.

Walks the routes of each deployed FastAPI app and fails on any non-GET route
that has neither an auth dependency nor an in-body call to a known auth guard.
Routes that are deliberately public must be allowlisted below with a reason.
"""
from __future__ import annotations

import ast
import builtins
import importlib
import importlib.util
import inspect
import textwrap
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import Depends, FastAPI, Header, routing as fastapi_routing
from fastapi.testclient import TestClient
from starlette.routing import Mount, Route

from stock_machine import api_v1_compat, automation_api, ibkr_bridge, prices_live, webapp_ops
from stock_machine.admin_panel import api as admin_panel_api
from stock_machine.agents import api as agents_api
from stock_machine.webapp_automation import app as vercel_app

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "a" * 32
SAFE_METHODS = {"GET", "HEAD"}

# pyproject [tool.vercel] entrypoint, plus the market-data bridge that is
# published through a tunnel next to TWS/IB Gateway.
DEPLOYED_APPS = {"vercel": vercel_app, "ibkr_bridge": ibkr_bridge.app}

# Callables that reject a request lacking valid credentials.
AUTH_GUARDS = {
    webapp_ops._require_admin, webapp_ops._require_processor,
    api_v1_compat._require_admin,
    automation_api._require_admin, automation_api._require_processor,
    agents_api.require_capture,
    admin_panel_api.owner,  # session cookie; origin + CSRF when write=True
    ibkr_bridge._authorize,
}

# Deliberately public non-GET routes and mounts (mounts are opaque ASGI apps,
# so every mount must be listed). Keep a reason next to each entry.
PUBLIC_WRITES = {
    # The owner login form itself: origin-checked; it issues the session.
    ("vercel", "POST", "/api/operator/login"),
    # Read-only MCP facade; POST is the MCP transport. It forwards only an
    # allowlisted set of public GET paths and copies no credentials.
    ("vercel", "MOUNT", "/mcp"),
    # Static UI assets; StaticFiles answers GET/HEAD only.
    ("vercel", "MOUNT", "/ui"),
}


def _entries(app):
    """Yield (method, path, entry) for every unsafe method and mount.

    Recent FastAPI keeps included routers lazy; iter_route_contexts flattens
    them with prefixes applied. Older releases already flatten app.routes.
    """
    contexts = getattr(fastapi_routing, "iter_route_contexts", None)
    for entry in (contexts(app.routes) if contexts else app.routes):
        route = getattr(entry, "original_route", entry)
        if isinstance(route, Mount):
            yield "MOUNT", entry.path, entry
        elif isinstance(route, Route):  # APIRoute and plain Starlette routes
            # methods=None (a class-based endpoint) accepts every method.
            methods = {"ANY"} if entry.methods is None else set(entry.methods)
            for method in sorted(methods - SAFE_METHODS):
                yield method, entry.path, entry
        else:  # fail closed: a new route kind needs a reviewed rule here
            raise AssertionError(f"unhandled route type {type(route).__name__}")


def _dependency_calls(dependant):
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _dependency_calls(dependency)


GUARD_NAMES = {guard.__name__ for guard in AUTH_GUARDS}


def _resolve(node, scope, imports):
    """The object an expression names, via function-local imports or globals."""
    if isinstance(node, ast.Name):
        if node.id in imports:
            module, attr = imports[node.id]
            return getattr(importlib.import_module(module), attr, None)
        return scope.get(node.id, getattr(builtins, node.id, None))
    if isinstance(node, ast.Attribute):
        owner = _resolve(node.value, scope, imports)
        return getattr(owner, node.attr, None)
    return None


def _called_guards(endpoint):
    """Known guards the endpoint, or a function nested in it, calls by name.

    Matching is by resolved object, not by name alone, so an unrelated helper
    that happens to share a guard's name does not count.
    """
    endpoint = inspect.unwrap(endpoint)
    tree = ast.parse(textwrap.dedent(inspect.getsource(endpoint)))
    package = getattr(inspect.getmodule(endpoint), "__package__", None)
    imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = (importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                      if node.level else node.module)
            for alias in node.names:
                imports[alias.asname or alias.name] = (module, alias.name)
    scope = getattr(endpoint, "__globals__", {})
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", None))
        if name in GUARD_NAMES and _resolve(node.func, scope, imports) in AUTH_GUARDS:
            yield name


def _guarded(entry) -> bool:
    dependant = getattr(entry, "dependant", None)
    if dependant is not None and any(c in AUTH_GUARDS for c in _dependency_calls(dependant)):
        return True
    endpoint = getattr(entry, "endpoint", None)
    return endpoint is not None and any(_called_guards(endpoint))


def _unauthenticated_writes(name, app):
    return sorted(
        (name, method, path) for method, path, entry in _entries(app)
        if method == "MOUNT" or not _guarded(entry))


@pytest.mark.parametrize("name", sorted(DEPLOYED_APPS))
def test_every_non_get_route_requires_auth(name):
    exposed = _unauthenticated_writes(name, DEPLOYED_APPS[name])
    unexpected = [route for route in exposed if route not in PUBLIC_WRITES]
    assert not unexpected, (
        "non-GET routes without an auth guard; add one, or allowlist the route "
        f"in PUBLIC_WRITES with a reason: {unexpected}")


def test_public_allowlist_has_no_stale_entries():
    exposed = {r for name, app in DEPLOYED_APPS.items() for r in _unauthenticated_writes(name, app)}
    assert PUBLIC_WRITES <= exposed


def test_walk_reaches_included_routers():
    writes = {(method, path) for method, path, _ in _entries(vercel_app)}
    assert {
        ("POST", "/api/prices/refresh"),                 # base dashboard app
        ("POST", "/api/admin/migrate"),                  # webapp_ops
        ("POST", "/api/v1/data-refresh"),                # api_v1 router
        ("POST", "/api/admin/automation/schedule"),      # automation router
        ("POST", "/api/admin/research/{ticker}/run"),    # research router
        ("POST", "/api/admin/agents/{ticker}/capture"),  # agent-lab router
        ("POST", "/api/operator/controls"),              # admin panel router
    } <= writes


def test_guard_detection_flags_unguarded_routes():
    def header_guard(authorization: str | None = Header(default=None)):
        webapp_ops._require_admin(authorization)

    probe = FastAPI()

    @probe.post("/open")
    def open_write():
        return {}

    @probe.post("/in-body")
    def in_body(authorization: str | None = Header(default=None)):
        api_v1_compat._require_admin(authorization)
        return {}

    @probe.post("/dependency", dependencies=[Depends(agents_api.require_capture)])
    def dependency():
        return {}

    @probe.post("/nested")
    def nested(authorization: str | None = Header(default=None)):
        header_guard(authorization)
        return {}

    # Only direct use of a known guard counts; a local wrapper does not.
    assert _unauthenticated_writes("probe", probe) == [
        ("probe", "POST", "/nested"), ("probe", "POST", "/open")]


@pytest.fixture
def refresh(monkeypatch):
    universe, many = Mock(return_value={"ok": 1}), Mock(return_value={"ok": 1})
    monkeypatch.setattr(prices_live, "refresh_universe", universe)
    monkeypatch.setattr(prices_live, "refresh_many", many)
    return TestClient(vercel_app), universe, many


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_price_refresh_rejects_anonymous_callers(refresh, monkeypatch, headers):
    monkeypatch.setenv("STOCK_MACHINE_ADMIN_TOKEN", TOKEN)
    client, universe, many = refresh
    assert client.post("/api/prices/refresh", headers=headers).status_code == 401
    universe.assert_not_called()
    many.assert_not_called()


def test_price_refresh_fails_closed_without_configured_token(refresh, monkeypatch):
    monkeypatch.delenv("STOCK_MACHINE_ADMIN_TOKEN", raising=False)
    client, universe, _ = refresh
    assert client.post("/api/prices/refresh").status_code == 503
    universe.assert_not_called()


def test_price_refresh_runs_for_admin(refresh, monkeypatch):
    monkeypatch.setenv("STOCK_MACHINE_ADMIN_TOKEN", TOKEN)
    client, universe, many = refresh
    auth = {"Authorization": "Bearer " + TOKEN}
    assert client.post("/api/prices/refresh", headers=auth).json() == {"ok": 1}
    universe.assert_called_once_with(10, "auto")
    assert client.post("/api/prices/refresh?ticker=AAPL", headers=auth).status_code == 200
    many.assert_called_once_with(["AAPL"], 10, "auto")


def test_public_ui_never_calls_price_refresh():
    for path in (ROOT / "webui").rglob("*"):
        if path.suffix in {".html", ".js"}:
            assert "/api/prices/refresh" not in path.read_text(encoding="utf-8"), path
