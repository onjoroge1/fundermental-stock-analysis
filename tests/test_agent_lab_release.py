"""Regression coverage for the schema declaration and native menu entry.

No production database, broker connection or capture is used by these tests.
"""
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from stock_machine import db

ROOT = Path(__file__).resolve().parents[1]


def test_declared_schema_matches_exactly_one_migration_head():
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert heads == [db.REQUIRED_SCHEMA_VERSION], (
        "A new migration must update REQUIRED_SCHEMA_VERSION in the same release"
    )


def _connection_with_versions(versions):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [(v,) for v in versions]
    return conn


@pytest.mark.parametrize("versions", [
    [], ["0018_consensus_archives"], ["unknown_future_revision"],
    ["0018_consensus_archives", "0019_agent_lab"],
])
def test_schema_guard_still_rejects_missing_old_unknown_and_multiple_heads(versions):
    conn = _connection_with_versions(versions)
    with pytest.raises(RuntimeError, match="Database migration required"):
        db.init_schema(conn)
    conn.commit.assert_not_called()


def test_current_schema_is_accepted():
    conn = _connection_with_versions([db.REQUIRED_SCHEMA_VERSION])
    db.init_schema(conn)
    conn.commit.assert_called_once()


class MenuLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nav_ids = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav":
            self.nav_ids.append(attrs.get("id", "navigation"))
        if tag == "a":
            self.links.append((attrs, tuple(self.nav_ids)))

    def handle_endtag(self, tag):
        if tag == "nav" and self.nav_ids:
            self.nav_ids.pop()


@pytest.mark.parametrize("filename", ["index.html", "trades.html", "agents.html"])
def test_agent_lab_has_one_native_navigation_link(filename):
    menu = MenuLinks()
    menu.feed((ROOT / "webui" / filename).read_text(encoding="utf-8"))
    links = [(attrs, ancestors) for attrs, ancestors in menu.links if attrs.get("href") == "/agents"]
    assert len(links) == 1
    attrs, ancestors = links[0]
    assert ancestors, "The entry belongs in navigation, not hidden in page content"
    assert "onclick" not in attrs and attrs.get("target", "_self") == "_self"
    if filename == "index.html":
        assert ancestors == ("nav-tools",), "Coverage redraws must not erase the entry"
    if filename == "agents.html":
        assert attrs.get("aria-current") == "page"


@pytest.mark.parametrize("path", ["/", "/trades", "/agents", "/agents/AAPL", "/agents/MSFT", "/agents/UBER", "/agents/HIMS", "/agents/VZ"])
def test_production_app_serves_menu_and_agent_pages_without_data_access(path, monkeypatch):
    from stock_machine.agents import journal
    from stock_machine.webapp_automation import app

    def forbidden(*args, **kwargs):
        pytest.fail("Page navigation must not access the database or capture research")

    monkeypatch.setattr(db, "connect", forbidden)
    monkeypatch.setattr(journal, "capture", forbidden)
    with TestClient(app) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'href="/agents"' in response.text


def test_responsive_navigation_asset_is_served():
    from stock_machine.webapp_automation import app
    with TestClient(app) as client:
        page = client.get("/")
        css = client.get("/ui/navigation.css")
    assert 'href="/ui/navigation.css"' in page.text
    assert css.status_code == 200
    assert "#nav-tools a:focus-visible" in css.text
    assert "@media (max-width: 760px)" in css.text
