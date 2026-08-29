from __future__ import annotations

from datetime import date, timedelta
from math import exp

from stock_machine.regime import RegimeFeatureProvider, sector_etf
from stock_machine.backtest.regime_shadow import coverage


def _series(n=220, daily=0.001, start="2025-01-01"):
    d0 = date.fromisoformat(start)
    px = 100.0
    rows = []
    for i in range(n):
        px *= exp(daily)
        rows.append({"date": (d0 + timedelta(days=i)).isoformat(),
                     "adj_close": px})
    return rows


def test_sector_etf_aliases():
    assert sector_etf("Information Technology") == "XLK"
    assert sector_etf("Technology") == "XLK"
    assert sector_etf("Health Care") == "XLV"
    assert sector_etf("unknown") is None


def test_regime_uses_only_rows_known_by_as_of():
    spy = _series(220, 0.001)
    qqq = _series(220, 0.0015)
    # Add a huge future move that must not affect an earlier observation.
    future = dict(qqq[-1])
    future["date"] = (date.fromisoformat(qqq[-1]["date"]) + timedelta(days=20)).isoformat()
    future["adj_close"] = qqq[-1]["adj_close"] * 10
    provider_a = RegimeFeatureProvider(spy_rows=spy, qqq_rows=qqq)
    provider_b = RegimeFeatureProvider(spy_rows=spy, qqq_rows=qqq + [future])
    as_of = spy[-1]["date"]
    assert provider_a.features_as_of(as_of)["vector"] == provider_b.features_as_of(as_of)["vector"]


def test_risk_on_label_for_broad_uptrend():
    spy = _series(220, 0.001)
    qqq = _series(220, 0.0015)
    universe = {f"T{i}": _series(220, 0.0005 + i * 0.00005) for i in range(10)}
    provider = RegimeFeatureProvider(spy_rows=spy, qqq_rows=qqq,
                                     sector_rows=_series(220, 0.0012),
                                     universe_rows=universe)
    r = provider.features_as_of(spy[-1]["date"])
    assert r["status"] == "OK"
    assert r["classification"] == "RISK_ON"
    assert r["features"]["has_qqq"] == 1.0
    assert r["features"]["has_sector"] == 1.0
    assert r["features"]["has_breadth"] == 1.0


def test_shadow_coverage_counts_missing_proxies_explicitly():
    rows = [
        {"ticker": "A", "regime": {"status": "OK", "classification": "MIXED",
         "features": {"has_qqq": 1.0, "has_sector": 0.0, "has_breadth": 1.0}}},
        {"ticker": "B", "regime": {"status": "INSUFFICIENT_MARKET_HISTORY",
         "classification": "UNKNOWN", "features": {}}},
    ]
    c = coverage(rows)
    assert c["observations"] == 2
    assert c["tickers"] == 2
    assert c["regime_ok_coverage"] == 0.5
    assert c["qqq_coverage"] == 0.5
    assert c["sector_proxy_coverage"] == 0.0
    assert c["breadth_coverage"] == 0.5
