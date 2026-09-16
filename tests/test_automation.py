from datetime import datetime, timezone

from fastapi.testclient import TestClient

import stock_machine.automation as automation


def test_choose_refresh_ticker_prefers_unindexed_then_stalest():
    companies = [{"ticker": "MSFT"}, {"ticker": "AAPL"}, {"ticker": "HIMS"}]
    indexed = [
        {"ticker": "AAPL", "indexed_at": "2026-08-30T10:00:00+00:00"},
        {"ticker": "MSFT", "indexed_at": "2026-08-29T10:00:00+00:00"},
    ]
    assert automation.choose_refresh_ticker(companies, indexed) == "HIMS"

    indexed.append({"ticker": "HIMS", "indexed_at": "2026-08-30T11:00:00+00:00"})
    assert automation.choose_refresh_ticker(companies, indexed) == "MSFT"


def test_cron_route_requires_processor_secret(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "abcdef0123456789abcdef0123456789")
    monkeypatch.delenv("STOCK_MACHINE_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(
        automation,
        "cron_tick",
        lambda: {"status": "OK", "scheduler": {"scheduled_count": 0}, "processor": {"status": "IDLE"}},
    )
    from stock_machine.webapp_automation import app

    client = TestClient(app, raise_server_exceptions=False)
    denied = client.get("/api/admin/cron")
    assert denied.status_code == 401

    allowed = client.get(
        "/api/admin/cron",
        headers={"Authorization": "Bearer abcdef0123456789abcdef0123456789"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["processor"]["status"] == "IDLE"


def test_price_cron_requires_secret_and_refreshes_bounded_shard(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "abcdef0123456789abcdef0123456789")
    monkeypatch.delenv("STOCK_MACHINE_ADMIN_TOKEN", raising=False)

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    captured = {}

    def fake_refresh(conn, tickers, **kwargs):
        captured.update(tickers=list(tickers), kwargs=kwargs)
        return {"status": "OK", "requested": len(tickers), "refreshed": len(tickers),
                "failures": [], "results": [], "health": {"status": "HEALTHY"}}

    from stock_machine import db, market_health
    monkeypatch.setattr(db, "connect", lambda: FakeConn())
    monkeypatch.setattr(
        db,
        "list_companies",
        lambda conn: [{"ticker": ticker} for ticker in ("VZ", "AAPL", "MSFT", "HIMS")],
    )
    monkeypatch.setattr(market_health, "refresh_prices", fake_refresh)

    from stock_machine.webapp_automation import app
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/api/admin/prices/cron/1/2").status_code == 401
    response = client.get(
        "/api/admin/prices/cron/1/2",
        headers={"Authorization": "Bearer abcdef0123456789abcdef0123456789"},
    )
    assert response.status_code == 200
    assert response.json()["batch"] == ["HIMS", "MSFT"]
    assert captured == {
        "tickers": ["HIMS", "MSFT"],
        "kwargs": {"only_if_stale": True, "limit": 2},
    }


def test_price_cron_surfaces_partial_refresh_as_failure(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "abcdef0123456789abcdef0123456789")

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    from stock_machine import db, market_health
    monkeypatch.setattr(db, "connect", lambda: FakeConn())
    monkeypatch.setattr(db, "list_companies", lambda conn: [{"ticker": "AAPL"}])
    monkeypatch.setattr(
        market_health,
        "refresh_prices",
        lambda *args, **kwargs: {"status": "PARTIAL", "failures": [{"ticker": "AAPL"}]},
    )

    from stock_machine.webapp_automation import app
    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/admin/prices/cron/0/18",
        headers={"Authorization": "Bearer abcdef0123456789abcdef0123456789"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "PARTIAL"


def test_sunday_scheduler_never_auto_syncs_forward_paper(monkeypatch):
    scheduled = []

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(automation.db, "connect", lambda: FakeConn())
    monkeypatch.setattr(automation, "ensure_schema", lambda conn: None)
    monkeypatch.setattr(automation.db, "list_companies", lambda conn: [{"ticker": "HIMS"}])
    monkeypatch.setattr(automation, "research_index", lambda conn: [])
    monkeypatch.setattr(automation, "_has_forward_cohorts", lambda conn: True)

    def fake_enqueue(conn, job_type, **kwargs):
        scheduled.append(job_type)
        return {"job_type": job_type, "action": "created"}

    monkeypatch.setattr(automation, "enqueue", fake_enqueue)
    result = automation.schedule_due(datetime(2026, 8, 30, 12, tzinfo=timezone.utc))

    assert "research_index_refresh" in scheduled
    assert "strategy_lab_v2" in scheduled
    assert "forward_paper_mark" in scheduled
    assert "forward_paper_sync" not in scheduled
    assert result["safety"]["forward_paper_sync_automated"] is False
