"""HTTP contract tests, not a claim that a production account is entitled."""
import json
from datetime import datetime, timezone

import httpx
import pytest

from stock_machine.ingestion.massive import daily_observation, news_observation, MassiveUnavailable


def daily():
    return {"ticker": "AAPL", "adjusted": True, "status": "OK", "results": [
        {"t": int(datetime(2026, 9, 15, 4, tzinfo=timezone.utc).timestamp() * 1000),
         "o": 100, "h": 102, "l": 99, "c": 101, "v": 1000}]}


def client_for(payload, status=200):
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=payload)))


def test_key_is_header_only_and_daily_bar_is_explicitly_not_live(monkeypatch):
    monkeypatch.setenv("MASSIVE_API", "test-secret-never-persist")
    def respond(req):
        assert req.headers["Authorization"] == "Bearer test-secret-never-persist"
        assert "test-secret" not in str(req.url)
        assert req.url.params["adjusted"] == "true"
        return httpx.Response(200, json=daily())
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = daily_observation("AAPL", "2026-09-15", client=client)
    assert result["status"] == "OBSERVED"
    assert "not dividend-adjusted" in result["price_basis"]
    assert "test-secret" not in json.dumps(result)


@pytest.mark.parametrize("change", ["wrong_ticker", "stale", "nan", "invalid_ohlc", "pagination", "wrong_basis"])
def test_bad_bars_fail_closed(monkeypatch, change):
    monkeypatch.setenv("MASSIVE_API", "test-only")
    raw = daily()
    if change == "wrong_ticker": raw["ticker"] = "MSFT"
    if change == "stale": raw["results"][0]["t"] -= 86400000
    if change == "nan": raw["results"][0]["c"] = None
    if change == "invalid_ohlc": raw["results"][0]["l"] = 103
    if change == "pagination": raw["next_url"] = "https://untrusted.invalid/"
    if change == "wrong_basis": raw["adjusted"] = False
    with client_for(raw) as client, pytest.raises(MassiveUnavailable):
        daily_observation("AAPL", "2026-09-15", client=client)


def test_rate_limit_and_missing_key_are_not_fake_observations(monkeypatch):
    monkeypatch.delenv("MASSIVE_API", raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with pytest.raises(MassiveUnavailable, match="NOT_CONFIGURED"):
        daily_observation("AAPL", "2026-09-15")
    monkeypatch.setenv("MASSIVE_API_KEY", "test-only")
    with client_for({"error": "secret"}, 429) as client, pytest.raises(MassiveUnavailable, match="HTTP_429"):
        daily_observation("AAPL", "2026-09-15", client=client)


def test_news_is_bounded_metadata_not_a_verified_article(monkeypatch):
    monkeypatch.setenv("MASSIVE_API", "test-only")
    raw = {"results": [{"id": "test", "tickers": ["AAPL"], "published_utc": "2026-09-16T10:00:00Z",
                        "article_url": "https://example.invalid/news", "title": "Unreviewed test metadata"}]}
    with client_for(raw) as client:
        result = news_observation("AAPL", now=datetime(2026, 9, 16, 15, tzinfo=timezone.utc), client=client)
    assert result["articles"][0]["content_review"] == "UNREVIEWED_HEADLINE_METADATA"
