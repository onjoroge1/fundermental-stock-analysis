from datetime import datetime, timezone

from fastapi.testclient import TestClient

import stock_machine.ibkr_bridge as bridge
from stock_machine.market_data.models import (
    MarketDataAvailability,
    MarketQuote,
    SessionStatus,
    StrikeSet,
    UnderlyingContract,
)


TOKEN = "0123456789abcdef0123456789abcdef"


class FakeProvider:
    def __init__(self):
        self.closed = False
        self.underlying = UnderlyingContract(
            provider="ibkr_tws",
            symbol="SBUX",
            conid=123,
            name="Starbucks Corp",
            currency="USD",
            exchange="NASDAQ",
            has_options=True,
        )

    def close(self):
        self.closed = True

    def session_status(self):
        return SessionStatus(
            provider="ibkr_tws",
            connected=True,
            authenticated=True,
            message="paper TWS connected",
        )

    def quote_underlying(self, symbol: str):
        assert symbol == "SBUX"
        return MarketQuote(
            provider="ibkr_tws",
            conid=123,
            symbol="SBUX",
            as_of=datetime.now(timezone.utc),
            availability=MarketDataAvailability.DELAYED,
            bid=106.9,
            ask=107.1,
            last=107.0,
            mark=107.0,
        )

    def resolve_underlying(self, symbol: str):
        assert symbol == "SBUX"
        return self.underlying

    def available_expirations(self, symbol: str):
        assert symbol == "SBUX"
        return {
            "provider": "ibkr_tws",
            "symbol": "SBUX",
            "conid": 123,
            "months": [
                {"month": "AUG26", "expirations": ["20260821"], "standard": "20260821"}
            ],
            "strikes": [100.0, 105.0, 110.0],
        }

    def available_strikes(self, symbol: str, month: str):
        assert symbol == "SBUX"
        assert month == "AUG26"
        return StrikeSet(
            provider="ibkr_tws",
            underlying=self.underlying,
            month="AUG26",
            call_strikes=[100.0, 105.0, 110.0],
            put_strikes=[100.0, 105.0, 110.0],
        )


def _client(monkeypatch):
    monkeypatch.setenv("IBKR_BRIDGE_TOKEN", TOKEN)
    created = []

    def fake_get_provider(name):
        assert name == "tws"
        provider = FakeProvider()
        created.append(provider)
        return provider

    monkeypatch.setattr(bridge, "get_provider", fake_get_provider)
    return TestClient(bridge.app, raise_server_exceptions=False), created


def test_health_is_public_and_discloses_no_broker_state(monkeypatch):
    client, created = _client(monkeypatch)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "stock-machine-ibkr-bridge",
        "read_only": True,
    }
    assert created == []
    assert response.headers["cache-control"] == "no-store"


def test_bridge_rejects_missing_credentials(monkeypatch):
    client, created = _client(monkeypatch)
    response = client.get("/v1/quotes/SBUX")
    assert response.status_code == 401
    assert created == []


def test_bridge_returns_quote_with_valid_credentials_and_closes_provider(monkeypatch):
    client, created = _client(monkeypatch)
    response = client.get(
        "/v1/quotes/sbux",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json()["symbol"] == "SBUX"
    assert response.json()["mark"] == 107.0
    assert len(created) == 1
    assert created[0].closed is True


def test_bridge_exposes_expirations_not_generic_proxy(monkeypatch):
    client, _created = _client(monkeypatch)
    response = client.get(
        "/v1/options/SBUX/expirations",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json()["months"][0]["month"] == "AUG26"

    assert client.get(
        "/v1/accounts",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ).status_code == 404
    assert client.post(
        "/v1/orders",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={},
    ).status_code == 404
