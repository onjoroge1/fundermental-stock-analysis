from __future__ import annotations

import httpx
import pytest

from stock_machine.ingestion import prices_fmp


def test_fmp_bulk_normalizes_only_requested_exact_date(monkeypatch):
    monkeypatch.setattr(prices_fmp, "FMP_API_KEY", "secret")
    monkeypatch.setattr(prices_fmp, "save_raw", lambda *args, **kwargs: None)

    def get(*args, **kwargs):
        request = httpx.Request("GET", args[0])
        return httpx.Response(200, request=request, json=[
            {"symbol": "AAPL", "date": "2026-09-28", "open": 100,
             "high": 103, "low": 99, "close": 102, "adjClose": 101.5,
             "volume": 1000},
            {"symbol": "MSFT", "date": "2026-09-28", "close": 200,
             "volume": 2000},
            {"symbol": "OTHER", "date": "2026-09-28", "close": 1},
        ])

    monkeypatch.setattr(prices_fmp.httpx, "get", get)
    result = prices_fmp.fetch_eod_bulk("2026-09-28", ["AAPL", "MSFT"])
    assert sorted(result) == ["AAPL", "MSFT"]
    assert result["AAPL"]["adj_close"] == 101.5
    assert result["MSFT"]["adj_close"] == 200


def test_fmp_errors_never_include_key_or_response_body(monkeypatch):
    monkeypatch.setattr(prices_fmp, "FMP_API_KEY", "super-secret")

    def get(*args, **kwargs):
        request = httpx.Request("GET", args[0])
        return httpx.Response(429, request=request, text="super-secret quota detail")

    monkeypatch.setattr(prices_fmp.httpx, "get", get)
    with pytest.raises(prices_fmp.FmpPriceError) as exc:
        prices_fmp.fetch_daily("AAPL")
    assert str(exc.value) == "FMP_PRICE_HTTP_429"
    assert "super-secret" not in str(exc.value)
