from fastapi.testclient import TestClient

from stock_machine.control_plane_bootstrap import merge_research_universe, migrate_to_head


def test_sparse_research_index_never_hides_unindexed_companies():
    companies = [
        {"ticker": "AAPL", "legal_name": "Apple", "sector": "Hardware"},
        {"ticker": "HIMS", "legal_name": "Hims", "sector": "Healthcare Services"},
    ]
    indexed = [{"ticker": "HIMS", "price": 29.0, "composite_score": 61}]
    result = merge_research_universe(companies, indexed)
    assert result["count"] == 2
    assert result["indexed_count"] == 1
    assert result["pending_count"] == 1
    by_ticker = {row["ticker"]: row for row in result["stocks"]}
    assert by_ticker["AAPL"]["index_status"] == "PENDING"
    assert by_ticker["HIMS"]["index_status"] == "READY"
    assert by_ticker["HIMS"]["price"] == 29.0


def test_migration_helper_has_fixed_head_target(monkeypatch):
    import alembic.command

    seen = {}

    def fake_upgrade(config, revision):
        seen["revision"] = revision
        seen["config_file"] = config.config_file_name

    monkeypatch.setattr(alembic.command, "upgrade", fake_upgrade)
    result = migrate_to_head()
    assert result == {"status": "OK", "migration_target": "head"}
    assert seen["revision"] == "head"
    assert seen["config_file"].endswith("alembic.ini")


def test_admin_migrate_route_uses_fixed_helper(monkeypatch):
    token = "0123456789abcdef0123456789abcdef"
    monkeypatch.setenv("STOCK_MACHINE_ADMIN_TOKEN", token)
    import stock_machine.control_plane_bootstrap as bootstrap
    monkeypatch.setattr(
        bootstrap, "migrate_to_head",
        lambda: {"status": "OK", "migration_target": "head"},
    )
    from stock_machine.webapp_ops import app

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/admin/migrate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["migration_target"] == "head"
