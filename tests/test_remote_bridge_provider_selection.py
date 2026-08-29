from stock_machine.market_data import get_provider
from stock_machine.market_data.remote_bridge import RemoteBridgeMarketData


def test_get_provider_selects_remote_bridge(monkeypatch):
    monkeypatch.setenv("IBKR_PROVIDER", "remote_bridge")
    monkeypatch.setenv("IBKR_BRIDGE_BASE_URL", "https://bridge.example.test")
    monkeypatch.setenv(
        "IBKR_BRIDGE_TOKEN", "0123456789abcdef0123456789abcdef"
    )

    provider = get_provider()
    try:
        assert isinstance(provider, RemoteBridgeMarketData)
    finally:
        provider.close()
