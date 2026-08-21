from stock_machine import webapp


class _Connection:
    closed = False

    def close(self):
        self.closed = True


def test_strategy_lab_endpoint_is_read_only_and_pending(monkeypatch):
    conn = _Connection()
    monkeypatch.setattr(webapp.db, "connect", lambda: conn)
    monkeypatch.setattr(webapp.db, "latest_strategy_lab_run", lambda c: None)
    monkeypatch.setattr(webapp.db, "latest_backtest_run_id", lambda c: None)

    result = webapp.strategy_lab()

    assert result["status"] == "PENDING"
    assert conn.closed is True


def test_strategy_lab_endpoint_returns_persisted_result(monkeypatch):
    payload = {"status": "OK", "run_id": "strategy_1"}
    monkeypatch.setattr(webapp.db, "connect", lambda: _Connection())
    monkeypatch.setattr(
        webapp.db, "latest_strategy_lab_run", lambda c: payload,
    )
    monkeypatch.setattr(
        webapp.db, "latest_backtest_run_id", lambda c: "bt_1",
    )
    payload["source_backtest_run_id"] = "bt_1"
    assert webapp.strategy_lab() is payload


def test_strategy_lab_endpoint_refuses_stale_backtest_source(monkeypatch):
    payload = {"status": "OK", "source_backtest_run_id": "bt_old"}
    monkeypatch.setattr(webapp.db, "connect", lambda: _Connection())
    monkeypatch.setattr(
        webapp.db, "latest_strategy_lab_run", lambda c: payload,
    )
    monkeypatch.setattr(
        webapp.db, "latest_backtest_run_id", lambda c: "bt_new",
    )

    result = webapp.strategy_lab()

    assert result["status"] == "STALE"
    assert result["latest_backtest_run_id"] == "bt_new"
