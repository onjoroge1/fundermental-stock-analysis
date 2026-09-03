from __future__ import annotations

from datetime import datetime, timedelta, timezone

from stock_machine import market_health


class _Cursor:
    def __init__(self, latest_rows, snapshot_rows):
        self.latest_rows = latest_rows
        self.snapshot_rows = snapshot_rows
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        if "max(date)" in sql:
            self._rows = self.latest_rows
        elif "DISTINCT ON (ticker)" in sql:
            self._rows = self.snapshot_rows
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, latest_rows, snapshot_rows):
        self.latest_rows = latest_rows
        self.snapshot_rows = snapshot_rows

    def cursor(self):
        return _Cursor(self.latest_rows, self.snapshot_rows)


def test_health_marks_recent_snapshot_current(monkeypatch):
    now = datetime(2026, 9, 2, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(market_health, "_now_utc", lambda: now)
    monkeypatch.setattr(
        market_health.db,
        "list_companies",
        lambda conn: [{"ticker": "AAPL"}],
    )
    conn = _Conn(
        [("AAPL", "2026-09-02")],
        [("AAPL", now - timedelta(hours=2), "2026-09-02", "OK", {}, [])],
    )
    result = market_health.health(conn, max_age_hours=18)
    assert result["status"] == "HEALTHY"
    assert result["current_count"] == 1
    assert result["stale_count"] == 0
    assert result["tickers"][0]["state"] == "CURRENT"


def test_health_marks_old_snapshot_stale(monkeypatch):
    now = datetime(2026, 9, 2, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(market_health, "_now_utc", lambda: now)
    monkeypatch.setattr(
        market_health.db,
        "list_companies",
        lambda conn: [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
    )
    conn = _Conn(
        [("AAPL", "2026-09-01"), ("MSFT", "2026-09-02")],
        [
            ("AAPL", now - timedelta(hours=30), "2026-09-01", "OK", {}, []),
            ("MSFT", now - timedelta(hours=1), "2026-09-02", "OK", {}, []),
        ],
    )
    result = market_health.health(conn, max_age_hours=18)
    assert result["status"] == "STALE"
    assert result["stale_count"] == 1
    assert result["stale_tickers"] == ["AAPL"]


def test_health_marks_missing_price_dataset(monkeypatch):
    now = datetime(2026, 9, 2, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(market_health, "_now_utc", lambda: now)
    monkeypatch.setattr(
        market_health.db,
        "list_companies",
        lambda conn: [{"ticker": "AAPL"}],
    )
    result = market_health.health(_Conn([], []), max_age_hours=18)
    assert result["status"] == "ERROR"
    assert result["missing_count"] == 1
    assert result["tickers"][0]["state"] == "MISSING"
