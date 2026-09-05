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
- Expectations features are present ONLY where this system actually stored a
  point-in-time consensus vintage or a dated earnings surprise. Missing old
  history stays missing; no current estimate is carried backward.
- Sector labels are today's; P/E-history percentile is excluded from the
  historical valuation score (split-adjustment inconsistency); market cap uses
  contemporaneous unadjusted close × contemporaneous cover-page share count.
"""
from __future__ import annotations

import bisect
import calendar
from datetime import date, timedelta

from .. import db
from ..features import metrics
from ..features.scoring import score_all
from ..prediction_inputs import fetch_consensus_history, fetch_surprise_history
from .panel_expectations import expectations_as_of
from ..market_calendar import session_on_or_before, session_on_or_after, latest_completed_session

FRESHNESS_DAYS = 200
MIN_QUARTERS = 8
HORIZONS = {"fwd_3m_pct": 91, "fwd_6m_pct": 182, "fwd_12m_pct": 365}
HORIZON_MONTHS = {"fwd_3m_pct": 3, "fwd_6m_pct": 6, "fwd_12m_pct": 12}


def target_session(as_of: str, months: int) -> str:
    origin = date.fromisoformat(as_of)
    month = origin.month - 1 + months
    year, month = origin.year + month // 12, month % 12 + 1
    target = date(year, month, min(origin.day, calendar.monthrange(year, month)[1]))
    return session_on_or_after(target.isoformat())

CAVEATS = [
    "Universe is today's coverage list: survivorship-biased; absolute returns "
    "inflated, cross-sectional results indicative only.",
    "Thresholds hand-set in 2026 knowing these histories (in-sample flavor).",
    "Expectations are point-in-time only where stored vintages/events exist; "
    "older missing history is never backfilled from current estimates.",
    "Sector labels are current-day; pe_5y_percentile excluded from historical "
    "valuation scoring.",
    "Signals use the prior completed session; entry and exit use the first session close on/after each calendar boundary. Costs are applied separately by portfolio evaluation.",
    "Historical market-cap reconstruction requires complete split events and matching contemporaneous share units; vendor/event coverage must be audited.",
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
        self.actions = db.fetch_actions(conn, ticker)
        self.consensus_history = fetch_consensus_history(conn, ticker)
        self.surprise_history = fetch_surprise_history(conn, ticker)
        self._dates = [p["date"] for p in self.prices]

    def _price(self, d: str, field: str) -> float | None:
        i = bisect.bisect_right(self._dates, d) - 1
        if i < 0:
            return None
        row = self.prices[i]
        if (date.fromisoformat(d) - date.fromisoformat(row["date"])).days > 10:
            return None
        return row.get(field)

    def _outcome_price(self, target: str) -> float | None:
        session = session_on_or_before(target)
        if not self._dates or session > min(self._dates[-1], latest_completed_session()):
            return None
        i = bisect.bisect_left(self._dates, session)
        if i >= len(self._dates) or self._dates[i] != session:
            return None
        return self.prices[i].get("adj_close")

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
        feature_date = latest_completed_session(date.fromisoformat(as_of))
        price = self._price(feature_date, "close")
        adj_now = self._price(feature_date, "adj_close")
        if not price or not adj_now:
            return None
        # Vendor closes are split-adjusted. Reconstruct the original price
        # basis before multiplying by originally reported share counts.
        for action in self.actions:
            if action["action_type"] == "split" and action["date"] > feature_date:
                price *= float(action["value"])

        ttm = metrics.build_ttm(qs)
        prior_ttm = metrics.build_ttm(qs[:-4]) if len(qs) >= 12 else None
        share_rows = [s for s in self.shares
                      if s["available_at"] and s["available_at"] <= as_of
                      and s["as_of"] <= as_of]
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
            "capital_allocation": metrics.capital_allocation_metrics(ttm, qs, market_cap),
            "valuation": metrics.valuation_metrics(ttm, price, market_cap, ev),
        }
        scores = score_all(derived, None, self.sector)
        if scores["composite_score"] is None:
            return None

        year_ago = (date.fromisoformat(as_of) - timedelta(days=365)).isoformat()
        adj_prior = self._price(year_ago, "adj_close")
        forward, target_dates = {}, {}
        entry_date = session_on_or_after(as_of)
        adj_entry = self._outcome_price(entry_date)
        for key, months in HORIZON_MONTHS.items():
            target = target_session(as_of, months)
            adj_fwd = self._outcome_price(target)
            target_dates[key] = target
            forward[key] = (round((adj_fwd / adj_entry - 1) * 100, 2)
                            if adj_fwd and adj_entry else None)

        expectations = expectations_as_of(
            self.consensus_history, self.surprise_history, as_of
        )
        return {
            "ticker": self.ticker,
            "sector": self.sector,
            "as_of": as_of,
            "feature_market_date": feature_date,
            "forward_entry_date": entry_date,
            "execution_convention": "next_available_session_close.v1",
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
            "expectations": expectations,
            "forward": forward,
            "forward_target_dates": target_dates,
        }


def run(conn, start: str = "2014-01-01", end: str | None = None,
        tickers: list[str] | None = None, progress=None) -> tuple[list[dict], list[str]]:
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
