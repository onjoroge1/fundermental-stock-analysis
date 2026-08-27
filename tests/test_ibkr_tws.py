"""TWS client tests: pure logic only — no socket, no TWS required.

The connection path is exercised manually against a running TWS/Gateway;
these tests cover the parts that can silently corrupt data: tick mapping,
delayed-data detection, sentinel rejection, and read-only surface area.
"""
from __future__ import annotations

import pytest

pytest.importorskip("ibapi", reason="ibapi (TWS API) not installed")

from stock_machine.market_data.ibkr_tws import (  # noqa: E402
    PROVIDER,
    IBKRTWSMarketData,
    TWSSettings,
    _Wrapper,
)
from stock_machine.market_data.models import MarketDataAvailability  # noqa: E402


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("IBKR_TWS_PORT", "4002")
    monkeypatch.setenv("IBKR_TWS_CLIENT_ID", "42")
    s = TWSSettings.from_env()
    assert s.port == 4002 and s.client_id == 42
    assert s.host == "127.0.0.1"


def test_live_tick_mapping():
    w = _Wrapper()
    w.tickPrice(1, 1, 101.5, None)   # bid
    w.tickPrice(1, 2, 101.9, None)   # ask
    w.tickPrice(1, 4, 101.7, None)   # last
    assert w.ticks[1] == {"bid": 101.5, "ask": 101.9, "last": 101.7}
    assert not w.delayed.get(1), "live ticks must not flag delayed"


def test_delayed_tick_mapping_sets_flag():
    w = _Wrapper()
    w.tickPrice(2, 66, 55.0, None)   # delayed bid
    w.tickPrice(2, 67, 55.4, None)   # delayed ask
    assert w.ticks[2] == {"bid": 55.0, "ask": 55.4}
    assert w.delayed[2] is True, "delayed ticks must be labeled, not passed off as live"


def test_negative_sentinel_rejected():
    """ibapi sends -1 for 'no data' — storing it would fabricate a price."""
    w = _Wrapper()
    w.tickPrice(3, 1, -1.0, None)
    w.tickSize(3, 8, -1)
    assert w.ticks.get(3, {}) == {}


def test_benign_notices_do_not_end_request():
    """2104/2106 are 'market data farm connected' notices, not failures."""
    import threading
    w = _Wrapper()
    evt = threading.Event()
    w.done[9] = evt
    w.error(9, 0, 2104, "Market data farm connection is OK")
    assert not evt.is_set()
    w.error(9, 0, 200, "No security definition has been found")
    assert evt.is_set(), "real errors must release the waiter"


def test_client_exposes_no_order_methods():
    """Read-only by construction: no trading surface on the provider."""
    forbidden = {"placeOrder", "cancelOrder", "reqIds_order", "openOrder"}
    assert not (forbidden & set(dir(IBKRTWSMarketData)))
    assert PROVIDER == "ibkr_tws"


def test_availability_enum_values_exist():
    for name in ("REALTIME", "DELAYED", "UNKNOWN"):
        assert hasattr(MarketDataAvailability, name)
