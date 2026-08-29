"""Price-source selection: broker primary, Yahoo fallback, no silent mixing."""
from __future__ import annotations

import pytest

from stock_machine.ingestion.prices_tws import _parse_bar_date, merge_series


def test_bar_date_parsing():
    assert _parse_bar_date("20260827") == "2026-08-27"
    assert _parse_bar_date("20260827 16:00:00 US/Eastern") == "2026-08-27"


def test_merge_pairs_trades_with_adjusted():
    trades = [{"date": "2026-01-02", "open": 1.0, "high": 2.0, "low": 1.0,
               "close": 2.0, "volume": 10.0}]
    adjusted = [{"date": "2026-01-02", "close": 1.88}]
    row = merge_series(trades, adjusted)[0]
    assert row["close"] == 2.0        # unadjusted -> market cap
    assert row["adj_close"] == 1.88   # adjusted   -> returns


def test_missing_adjusted_bar_stays_null_not_substituted():
    """Substituting the unadjusted close would understate every
    dividend-period return — the field must stay null instead."""
    trades = [{"date": "2026-01-03", "open": 2.0, "high": 3.0, "low": 2.0,
               "close": 3.0, "volume": 20.0}]
    row = merge_series(trades, [])[0]
    assert row["adj_close"] is None
    assert row["close"] == 3.0


def test_auto_falls_back_to_yahoo_and_records_the_downgrade(monkeypatch):
    import stock_machine.pipeline as pipeline

    def broker_down(ticker, duration=None):
        raise RuntimeError("no TWS/Gateway handshake")

    monkeypatch.setattr(pipeline, "PRICE_SOURCE", "auto")
    monkeypatch.setattr("stock_machine.ingestion.prices_tws.fetch_daily",
                        broker_down)
    monkeypatch.setattr(pipeline.price_ing, "fetch_daily",
                        lambda t: ([{"date": "2026-01-02", "close": 1.0}],
                                   [{"date": "2026-01-02",
                                     "action_type": "split", "value": 4.0}]))
    rows, actions, source, events = pipeline._fetch_prices("AAPL")
    assert source == "yahoo" and rows and actions
    assert any(e["event"] == "PRICE_SOURCE_FALLBACK" for e in events), \
        "a downgrade to the fallback source must be recorded, not silent"


def test_forced_tws_raises_rather_than_downgrading(monkeypatch):
    """PRICE_SOURCE=tws means the broker or nothing: a silent Yahoo
    substitution would defeat the point of pinning the source."""
    import stock_machine.pipeline as pipeline

    def broker_down(ticker, duration=None):
        raise RuntimeError("gateway closed")

    monkeypatch.setattr(pipeline, "PRICE_SOURCE", "tws")
    monkeypatch.setattr("stock_machine.ingestion.prices_tws.fetch_daily",
                        broker_down)
    with pytest.raises(RuntimeError, match="gateway closed"):
        pipeline._fetch_prices("AAPL")


def test_yahoo_forced_skips_the_broker_entirely(monkeypatch):
    import stock_machine.pipeline as pipeline

    def must_not_run(ticker, duration=None):  # pragma: no cover
        raise AssertionError("broker must not be contacted when pinned to yahoo")

    monkeypatch.setattr(pipeline, "PRICE_SOURCE", "yahoo")
    monkeypatch.setattr("stock_machine.ingestion.prices_tws.fetch_daily",
                        must_not_run)
    monkeypatch.setattr(pipeline.price_ing, "fetch_daily",
                        lambda t: ([{"date": "2026-01-02", "close": 1.0}], []))
    _, _, source, events = pipeline._fetch_prices("AAPL")
    assert source == "yahoo" and events == []
