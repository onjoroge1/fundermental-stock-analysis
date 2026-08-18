from urllib.parse import parse_qs

import httpx
import pytest

from stock_machine.market_data.ibkr_client_portal import (
    IBKRClientPortalMarketData,
    IBKRClientPortalSettings,
)
from stock_machine.market_data.models import MarketDataAvailability


def provider(handler):
    settings = IBKRClientPortalSettings(
        base_url="https://localhost:5000/v1/api",
        verify_ssl=False,
        snapshot_wait_s=0,
        min_request_interval_s=0,
    )
    client = httpx.Client(
        base_url=settings.base_url,
        transport=httpx.MockTransport(handler),
    )
    return IBKRClientPortalMarketData(
        settings, client=client, sleeper=lambda _seconds: None
    )


def test_session_status_is_normalized():
    def handler(request):
        assert request.url.path.endswith("/iserver/auth/status")
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={
                "connected": True,
                "authenticated": True,
                "competing": False,
                "message": "",
            },
        )

    status = provider(handler).session_status()
    assert status.connected is True
    assert status.authenticated is True


def test_option_chain_follows_discovery_and_snapshot_sequence():
    paths = []
    snapshot_calls = 0

    def handler(request):
        nonlocal snapshot_calls
        path = request.url.path
        paths.append(path)
        query = parse_qs(request.url.query.decode())
        if path.endswith("/iserver/secdef/search"):
            return httpx.Response(200, json=[{
                "conid": 265598,
                "symbol": "AAPL",
                "companyName": "APPLE INC",
                "currency": "USD",
                "listingExchange": "NASDAQ",
                "sections": [{"secType": "OPT", "months": "AUG26;SEP26"}],
            }])
        if path.endswith("/iserver/secdef/strikes"):
            return httpx.Response(
                200, json={"call": [245, 250, 255], "put": [245, 250, 255]}
            )
        if path.endswith("/iserver/secdef/info"):
            right = query["right"][0]
            conid = 9001 if right == "C" else 9002
            return httpx.Response(200, json=[{
                "conid": conid,
                "maturityDate": "20260821",
                "strike": 250,
                "right": right,
                "multiplier": "100",
                "currency": "USD",
                "exchange": "SMART",
            }])
        if path.endswith("/iserver/accounts"):
            return httpx.Response(200, json={"accounts": ["U123"]})
        if path.endswith("/iserver/marketdata/snapshot"):
            snapshot_calls += 1
            conids = [int(value) for value in query["conids"][0].split(",")]
            rows = []
            for conid in conids:
                if conid == 265598:
                    rows.append({
                        "conid": conid,
                        "55": "AAPL",
                        "31": "250.10",
                        "84": "250.05",
                        "86": "250.15",
                        "6509": "RpB",
                        "_updated": 1786903200000,
                    })
                else:
                    rows.append({
                        "conid": conid,
                        "31": "5.20",
                        "84": "5.10",
                        "86": "5.30",
                        "6509": "DpB",
                        "7308": "0.51" if conid == 9001 else "-0.49",
                        "7309": "0.04",
                        "7310": "-0.08",
                        "7311": "0.12",
                        "7633": "32.5",
                        "7635": "5.20",
                        "7638": "1200",
                        "_updated": 1786903200000,
                    })
            return httpx.Response(200, json=rows)
        return httpx.Response(404, json={"error": path})

    chain = provider(handler).option_chain("aapl", "aug26", [250])
    assert chain.underlying.conid == 265598
    assert len(chain.options) == 2
    assert chain.options[0].implied_volatility == pytest.approx(0.325)
    assert chain.options[0].quote.availability == MarketDataAvailability.DELAYED
    assert "market data is delayed" in chain.options[0].quote.warnings
    assert snapshot_calls == 3  # underlying preflight + quote, then option batch
    assert all("orders" not in path for path in paths)
    assert paths.index("/v1/api/iserver/secdef/search") < paths.index(
        "/v1/api/iserver/secdef/strikes"
    )


def test_remote_gateway_cannot_disable_tls_verification():
    settings = IBKRClientPortalSettings(
        base_url="https://example.com/v1/api", verify_ssl=False
    )
    with pytest.raises(ValueError, match="local IBKR gateway"):
        settings.validate()


def test_chain_requires_a_bounded_explicit_strike_selection():
    p = provider(lambda _request: httpx.Response(500))
    with pytest.raises(ValueError, match="1-20"):
        p.option_chain("AAPL", "AUG26", [])
    with pytest.raises(ValueError, match="1-20"):
        p.option_chain("AAPL", "AUG26", range(21))
