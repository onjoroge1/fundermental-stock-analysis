import sys
import types

import pytest

from stock_machine.market_data import MarketDataUnavailable, get_provider


def test_tws_connection_failure_is_normalized(monkeypatch):
    fake_module = types.ModuleType("stock_machine.market_data.ibkr_tws")

    class FakeTWS:
        def connect(self):
            raise ConnectionError("gateway offline")

    fake_module.IBKRTWSMarketData = FakeTWS
    monkeypatch.setitem(sys.modules, "stock_machine.market_data.ibkr_tws", fake_module)

    with pytest.raises(MarketDataUnavailable) as exc_info:
        get_provider("tws")

    message = str(exc_info.value)
    assert "tws provider unavailable" in message
    assert "ConnectionError" in message
    assert "gateway offline" in message


def test_unknown_provider_remains_configuration_error():
    with pytest.raises(ValueError, match="unknown market-data provider"):
        get_provider("not-a-provider")
