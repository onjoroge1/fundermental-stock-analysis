from datetime import datetime, timezone

import httpx
import pytest

from stock_machine.market_data.remote_bridge import (
    RemoteBridgeMarketData,
    RemoteBridgeSettings,
)


TOKEN = "0123456789abcdef0123456789abcdef"


def _settings() -> RemoteBridgeSettings:
    return RemoteBridgeSettings(
        base_url="https://bridge.example.test",
        token=TOKEN,
        timeout_s=5,
        verify_ssl=True,
    )


def test_remote_bridge_rejects_insecure_remote_url():
    settings = RemoteBridgeSettings(
        base_url="http://bridge.example.test",
        token=TOKEN,
    )
    with pytest.raises(ValueError, match="must use HTTPS"):
        settings.validate()


def test_remote_bridge_sends_bearer_auth_and_parses_quote():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "provider": "ibkr_tws",
                "conid": 123,
                "symbol": "SBUX",
                "as_of": datetime.now(timezone.utc).isoformat(),
                "availability": "delayed",
                "bid": 106.9,
                "ask": 107.1,
                "last": 107.0,
                "mark": 107.0,
                "warnings": [],
            },
        )

    client = httpx.Client(
        base_url="https://bridge.example.test",
        transport=httpx.MockTransport(handler),
    )
    provider = RemoteBridgeMarketData(_settings(), client=client)
    quote = provider.quote_underlying("sbux")

    assert seen["authorization"] == f"Bearer {TOKEN}"
    assert seen["path"] == "/v1/quotes/SBUX"
    assert quote.symbol == "SBUX"
    assert quote.mark == 107.0
    assert quote.availability.value == "delayed"


def test_remote_bridge_option_chain_is_bounded():
    client = httpx.Client(
        base_url="https://bridge.example.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    provider = RemoteBridgeMarketData(_settings(), client=client)

    with pytest.raises(ValueError, match="1-20 strikes"):
        provider.option_chain("SBUX", "AUG26", [])

    with pytest.raises(ValueError, match="1-20 strikes"):
        provider.option_chain("SBUX", "AUG26", list(range(1, 22)))
