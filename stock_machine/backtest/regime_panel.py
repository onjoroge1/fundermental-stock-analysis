"""Attach causal regime features to the existing point-in-time backtest panel."""
from __future__ import annotations

from collections import defaultdict

from .. import db
from ..regime import RegimeFeatureProvider, sector_etf


def _price_rows(rows: list[dict]) -> list[dict]:
    return [
        {"date": r["date"], "close": r.get("close"),
         "adj_close": r.get("adj_close") or r.get("close")}
        for r in rows
        if r.get("adj_close") is not None or r.get("close") is not None
    ]


def enrich(conn, observations: list[dict]) -> tuple[list[dict], dict]:
    """Return copied observations with a ``regime`` field.

    All proxy histories are read from the normalized price store.  If QQQ or a
    sector ETF has not been ingested, the provider emits explicit missingness
    flags rather than substituting another series.
    """
    companies = {c["ticker"]: c for c in db.list_companies(conn)}
    tickers = sorted({row["ticker"] for row in observations})
    universe_rows = {t: _price_rows(db.fetch_prices(conn, t)) for t in tickers}
    spy_rows = _price_rows(db.fetch_prices(conn, "SPY"))
    qqq_rows = _price_rows(db.fetch_prices(conn, "QQQ"))

    needed_sector_etfs = {
        sector_etf((companies.get(t) or {}).get("sector")) for t in tickers
    } - {None}
    sector_rows = {
        etf: _price_rows(db.fetch_prices(conn, etf)) for etf in needed_sector_etfs
    }

    providers = {}
    for ticker in tickers:
        sector = (companies.get(ticker) or {}).get("sector")
        etf = sector_etf(sector)
        providers[ticker] = RegimeFeatureProvider(
            spy_rows=spy_rows,
            qqq_rows=qqq_rows,
            sector_rows=sector_rows.get(etf, []),
            universe_rows=universe_rows,
        )

    enriched = []
    statuses = defaultdict(int)
    classifications = defaultdict(int)
    for row in observations:
        regime = providers[row["ticker"]].features_as_of(row["as_of"])
        statuses[regime["status"]] += 1
        classifications[regime["classification"]] += 1
        copy = dict(row)
        copy["regime"] = regime
        enriched.append(copy)

    coverage = {
        "observations": len(enriched),
        "spy_rows": len(spy_rows),
        "qqq_rows": len(qqq_rows),
        "sector_etfs_requested": sorted(needed_sector_etfs),
        "sector_etfs_with_history": sorted(k for k, v in sector_rows.items() if v),
        "regime_status_counts": dict(statuses),
        "classification_counts": dict(classifications),
    }
    return enriched, coverage
