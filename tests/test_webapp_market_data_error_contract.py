from fastapi import FastAPI
from fastapi.testclient import TestClient

from stock_machine.market_data import MarketDataUnavailable


def test_market_data_unavailable_renders_structured_503():
    # Import the production app only after the exception type so this test
    # exercises the registered operational handler rather than reimplementing
    # its behavior.
    from stock_machine.webapp_ops import app

    probe_path = "/__test_market_data_unavailable"
    if not any(getattr(route, "path", None) == probe_path for route in app.routes):
        @app.get(probe_path)
        def probe():
            raise MarketDataUnavailable("tws provider unavailable: gateway offline")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(probe_path)
    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["service"] == "market_data"
    assert payload["retryable"] is True
    assert payload["path"] == probe_path
