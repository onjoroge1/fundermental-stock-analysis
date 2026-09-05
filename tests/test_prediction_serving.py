from stock_machine import webapp


class _Connection:
    def close(self):
        pass


def test_prediction_endpoint_is_read_only_and_pending(monkeypatch):
    monkeypatch.setattr(webapp.db, "connect", lambda: _Connection())
    monkeypatch.setattr(
        webapp.db, "fetch_prices",
        lambda conn, ticker: [{"date": "2026-08-20"}],
    )
    monkeypatch.setattr(
        webapp.db, "latest_prediction_forecast", lambda conn, ticker: None,
    )
    result = webapp.predict("aapl")
    assert result["status"] == "PENDING"
    assert result["ticker"] == "AAPL"


def test_prediction_endpoint_refuses_stale_vintage(monkeypatch):
    from stock_machine.prediction import MODEL_VERSION

    monkeypatch.setattr(webapp.db, "connect", lambda: _Connection())
    monkeypatch.setattr(
        webapp.db, "fetch_prices",
        lambda conn, ticker: [{"date": "2026-08-20"}],
    )
    monkeypatch.setattr(
        webapp.db,
        "latest_prediction_forecast",
        lambda conn, ticker: {
            "status": "OK", "ticker": ticker, "as_of": "2026-08-19",
            "model_version": MODEL_VERSION,
        },
    )
    result = webapp.predict("AAPL")
    assert result["status"] == "STALE"
    assert result["latest_price_date"] == "2026-08-20"


def test_prediction_endpoint_returns_current_completed_vintage(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr("stock_machine.market_calendar.market_now", lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc))
    from stock_machine.prediction import MODEL_VERSION

    payload = {
        "status": "OK", "ticker": "AAPL", "as_of": "2026-08-20",
        "model_version": MODEL_VERSION,
    }
    monkeypatch.setattr(webapp.db, "connect", lambda: _Connection())
    monkeypatch.setattr(
        webapp.db, "fetch_prices",
        lambda conn, ticker: [{"date": "2026-08-20"}],
    )
    monkeypatch.setattr(
        webapp.db, "latest_prediction_forecast", lambda conn, ticker: payload,
    )
    assert webapp.predict("AAPL") is payload
