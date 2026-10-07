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
        [("AAPL", now - timedelta(hours=2), "2026-09-02", "PASS", {}, [], now - timedelta(hours=2))],
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
            ("AAPL", now - timedelta(hours=30), "2026-09-01", "PASS", {}, [], now - timedelta(hours=2)),
            ("MSFT", now - timedelta(hours=1), "2026-09-02", "PASS", {}, [], now - timedelta(hours=2)),
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


def test_refresh_classifies_failure_recovered_by_final_health(monkeypatch):
    states = iter([
        {"stale_tickers": ["AAPL"], "tickers": [{"ticker": "AAPL", "state": "STALE"}]},
        {"status": "HEALTHY", "stale_tickers": [], "tickers": [{"ticker": "AAPL", "state": "CURRENT"}]},
    ])
    monkeypatch.setattr(market_health, "health", lambda *a, **k: next(states))
    def fail_fetch(ticker):
        raise RuntimeError("provider")
    monkeypatch.setattr(market_health, "_fetch_prices", fail_fetch)
    class Conn:
        def rollback(self): pass
    result = market_health.refresh_prices(Conn(), ["AAPL"])
    assert result["status"] == "PARTIAL_RECOVERED"
    assert result["unresolved_failures"] == []


def test_refresh_classifies_unresolved_stale_failure(monkeypatch):
    states = iter([
        {"stale_tickers": ["AAPL"], "tickers": [{"ticker": "AAPL", "state": "STALE"}]},
        {"status": "STALE", "stale_tickers": ["AAPL"], "tickers": [{"ticker": "AAPL", "state": "STALE"}]},
    ])
    monkeypatch.setattr(market_health, "health", lambda *a, **k: next(states))
    def fail_fetch(ticker):
        raise RuntimeError("provider")
    monkeypatch.setattr(market_health, "_fetch_prices", fail_fetch)
    class Conn:
        def rollback(self): pass
    result = market_health.refresh_prices(Conn(), ["AAPL"])
    assert result["status"] == "ACTUAL_STALE_FAILURE"
    assert result["unresolved_failures"][0]["ticker"] == "AAPL"
    assert result["unresolved_failures"][0]["error"] == "RuntimeError: provider"


def test_health_includes_requested_benchmarks_outside_company_universe(monkeypatch):
    now = datetime(2026, 9, 2, 21, tzinfo=timezone.utc)
    monkeypatch.setattr(market_health, "_now_utc", lambda: now)
    monkeypatch.setattr(market_health.db, "list_companies", lambda c: [{"ticker": "AAPL"}])
    conn = _Conn([("AAPL", "2026-09-02"), ("SPY", "2026-09-01")], [])
    result = market_health.health(conn, tickers=["SPY", "QQQ"])
    assert result["ticker_count"] == 2
    assert result["stale_tickers"] == ["SPY", "QQQ"]
    assert result["stale_count"] == 1 and result["missing_count"] == 1


def test_refresh_cli_accepts_verified_recovery(monkeypatch):
    import sys
    from scripts import refresh_prices
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(sys, "argv", ["refresh_prices.py", "SPY"])
    monkeypatch.setattr(refresh_prices.db, "connect", lambda: Conn())
    monkeypatch.setattr(refresh_prices, "health", lambda *a, **k: {})
    monkeypatch.setattr(refresh_prices, "refresh_prices", lambda *a, **k: {"status": "PARTIAL_RECOVERED"})
    assert refresh_prices.main() == 0
