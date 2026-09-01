from stock_machine.market_data import configured_provider_name, get_provider
from stock_machine.market_data.remote_bridge import RemoteBridgeMarketData


def _set_bridge_env(monkeypatch):
    monkeypatch.setenv("IBKR_BRIDGE_BASE_URL", "https://bridge.example.test")
    monkeypatch.setenv(
        "IBKR_BRIDGE_TOKEN", "0123456789abcdef0123456789abcdef"
    )


def test_get_provider_selects_remote_bridge(monkeypatch):
    monkeypatch.setenv("IBKR_PROVIDER", "remote_bridge")
    _set_bridge_env(monkeypatch)

    provider = get_provider()
    try:
        assert isinstance(provider, RemoteBridgeMarketData)
    finally:
        provider.close()


def test_provider_auto_selects_remote_bridge_when_bridge_is_configured(monkeypatch):
    monkeypatch.delenv("IBKR_PROVIDER", raising=False)
    _set_bridge_env(monkeypatch)

    assert configured_provider_name() == "remote_bridge"
    provider = get_provider()
    try:
        assert isinstance(provider, RemoteBridgeMarketData)
    finally:
        provider.close()


def test_explicit_provider_override_beats_bridge_inference(monkeypatch):
    monkeypatch.delenv("IBKR_PROVIDER", raising=False)
    _set_bridge_env(monkeypatch)

    assert configured_provider_name("client_portal") == "client_portal"


def test_legacy_default_remains_tws_without_bridge_configuration(monkeypatch):
    monkeypatch.delenv("IBKR_PROVIDER", raising=False)
    monkeypatch.delenv("IBKR_BRIDGE_BASE_URL", raising=False)
    monkeypatch.delenv("IBKR_BRIDGE_TOKEN", raising=False)

    assert configured_provider_name() == "tws"
