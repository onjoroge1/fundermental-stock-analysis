from datetime import date

from stock_machine import webapp


class _Connection:
    closed = False

    def close(self):
        self.closed = True


def _patch(monkeypatch, screen, lab=None, backtest="bt_1"):
    conn = _Connection()
    monkeypatch.setattr(webapp.db, "connect", lambda: conn)
    monkeypatch.setattr(webapp.db, "latest_strategy_screen", lambda c: screen)
    monkeypatch.setattr(webapp.db, "latest_strategy_lab_run", lambda c: lab)
    monkeypatch.setattr(webapp.db, "latest_backtest_run_id", lambda c: backtest)
    return conn


def test_strategy_screen_endpoint_pending_and_read_only(monkeypatch):
    conn = _patch(monkeypatch, None)
    result = webapp.strategy_screen()
    assert result["status"] == "PENDING"
    assert conn.closed is True


def test_strategy_screen_endpoint_rejects_stale_policy_source(monkeypatch):
    screen = {"status": "OK", "screen_id": "s1",
              "strategy_lab_run_id": "old", "source_backtest_run_id": "bt_1"}
    _patch(monkeypatch, screen, {"run_id": "new"})
    assert webapp.strategy_screen()["status"] == "STALE"


def test_strategy_screen_endpoint_returns_current_persisted_screen(monkeypatch):
    screen = {"status": "OK", "strategy_lab_run_id": "lab_1",
              "source_backtest_run_id": "bt_1",
              "as_of": date.today().isoformat()}
    _patch(monkeypatch, screen, {"run_id": "lab_1"})
    assert webapp.strategy_screen() is screen
