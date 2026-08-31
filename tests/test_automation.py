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

    assert "ticker_refresh" in scheduled
    assert "strategy_lab_v2" in scheduled
    assert "forward_paper_mark" in scheduled
    assert "forward_paper_sync" not in scheduled
    assert result["safety"]["forward_paper_sync_automated"] is False
