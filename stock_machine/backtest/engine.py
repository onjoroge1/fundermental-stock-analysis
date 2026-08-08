"""Walk-forward backtest engine.

Replays the scoring engine over a historical date grid using ONLY data whose
available_at precedes each as-of date (same no-lookahead rule as bundles),
then measures forward returns on the adjusted close.

Honesty constraints stamped into every run:
- The universe is today's coverage list — SURVIVORSHIP-BIASED. Absolute
  returns are inflated; cross-sectional (rank) results are less affected but
  not immune.
- Scoring thresholds were hand-set in 2026 with knowledge of these companies'
  histories — results have an in-sample flavor and are indicative, not proof.
- No historical consensus exists, so the expectations component is absent and
  weights renormalize (matches how the engine would have run).
- Sector labels are today's; P/E-history percentile is excluded from the
  historical valuation score (split-adjustment inconsistency); market cap uses
  contemporaneous unadjusted close × contemporaneous cover-page share count.
"""
from __future__ import annotations

import bisect
from datetime import date, timedelta

from .. import db
from ..features import metrics
from ..features.scoring import score_all

FRESHNESS_DAYS = 200   # latest known quarter must end within this window
MIN_QUARTERS = 8
HORIZONS = {"fwd_3m_pct": 91, "fwd_6m_pct": 182, "fwd_12m_pct": 365}

CAVEATS = [
    "Universe is today's 43 names: survivorship-biased; absolute returns "
    "inflated, cross-sectional results indicative only.",
    "Thresholds hand-set in 2026 knowing these histories (in-sample flavor).",
    "Expectations component absent historically; weights renormalized.",
    "Sector labels are current-day; pe_5y_percentile excluded from "
    "historical valuation scoring.",
]


def quarterly_grid(start: str, end: str) -> list[str]:
    out = []
    d = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    while d <= stop:
        out.append(d.isoformat())
        m = d.month + 3
        d = date(d.year + (m - 1) // 12, (m - 1) % 12 + 1, 1)
    return out


class TickerData:
    def __init__(self, conn, ticker: str):
        self.ticker = ticker
        company = db.fetch_company(conn, ticker) or {}
        self.sector = company.get("sector")
        self.periods = db.fetch_periods(conn, ticker, "quarter")
        self.prices = db.fetch_prices(conn, ticker)
        self.shares = db.fetch_shares(conn, ticker)
        self._dates = [p["date"] for p in self.prices]

    def _price(self, d: str, field: str) -> float | None:
        i = bisect.bisect_right(self._dates, d) - 1
        if i < 0:
            return None
        row = self.prices[i]
        if (date.fromisoformat(d) - date.fromisoformat(row["date"])).days > 10:
            return None  # stale lookup — no price near this date
        return row.get(field) or row["close"]

    def observe(self, as_of: str) -> dict | None:
        qs = [p for p in self.periods
              if p["available_at"] and p["available_at"] <= as_of]
        if len(qs) < MIN_QUARTERS:
            return None
        latest = qs[-1]
        age = (date.fromisoformat(as_of)
               - date.fromisoformat(latest["period_end"])).days
        if age > FRESHNESS_DAYS:
            return None
        price = self._price(as_of, "close")
        adj_now = self._price(as_of, "adj_close")
        if not price or not adj_now:
            return None

        ttm = metrics.build_ttm(qs)
        prior_ttm = metrics.build_ttm(qs[:-4]) if len(qs) >= 12 else None
        share_rows = [s for s in self.shares
                      if s["available_at"] and s["available_at"] <= as_of]
        shares = share_rows[-1]["shares"] if share_rows else None
        if not shares and ttm:
            shares = ttm["fields"].get("weighted_average_diluted_shares")
        market_cap = price * shares if shares else None
        nd = metrics.net_debt(latest)
        ev = (market_cap + nd) if market_cap is not None and nd is not None else None

        derived = {
            "growth": metrics.growth_metrics(qs, []),
            "profitability": metrics.profitability_metrics(ttm, prior_ttm),
            "earnings_quality": metrics.earnings_quality_metrics(ttm, qs),
            "financial_health": metrics.financial_health_metrics(latest, ttm),
            "capital_allocation": metrics.capital_allocation_metrics(
                ttm, qs, market_cap),
            "valuation": metrics.valuation_metrics(
                ttm, price, market_cap, ev),  # no pe-percentile history
        }
        scores = score_all(derived, None, self.sector)
        if scores["composite_score"] is None:
            return None

        year_ago = (date.fromisoformat(as_of) - timedelta(days=365)).isoformat()
        adj_prior = self._price(year_ago, "adj_close")
        forward = {}
        for key, days in HORIZONS.items():
            target = (date.fromisoformat(as_of) + timedelta(days=days)).isoformat()
            adj_fwd = self._price(target, "adj_close")
            forward[key] = (round((adj_fwd / adj_now - 1) * 100, 2)
                            if adj_fwd else None)
        return {
            "ticker": self.ticker,
            "sector": self.sector,
            "as_of": as_of,
            "composite": scores["composite_score"],
            "components": scores["components"],
            "factors": {
                "earnings_yield_pct": derived["valuation"]["earnings_yield_pct"],
                "fcf_yield_pct": derived["valuation"]["fcf_yield_pct"],
                "revenue_yoy_pct": derived["growth"]["revenue_yoy_pct"],
                "roic_pct": derived["profitability"]["roic_pct"],
                "momentum_12m_pct": (round((adj_now / adj_prior - 1) * 100, 2)
                                     if adj_prior else None),
            },
            "forward": forward,
        }


def run(conn, start: str = "2014-01-01", end: str | None = None,
        tickers: list[str] | None = None,
        progress=None) -> tuple[list[dict], list[str]]:
    """Returns (observations, grid)."""
    if end is None:
        end = (date.today() - timedelta(days=85)).isoformat()
    grid = quarterly_grid(start, end)
    if tickers is None:
        tickers = [c["ticker"] for c in db.list_companies(conn)]
    observations = []
    for t in tickers:
        td = TickerData(conn, t)
        n = 0
        for as_of in grid:
            obs = td.observe(as_of)
            if obs:
                observations.append(obs)
                n += 1
        if progress:
            progress(t, n)
    return observations, grid
