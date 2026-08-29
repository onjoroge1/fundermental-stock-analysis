"""Causal market/sector regime features for alpha research.

P1 deliberately starts with series the stock machine can already store in the
same point-in-time daily price table.  No external macro number is invented or
backfilled.  Missing QQQ/sector/breadth history is represented explicitly.

Every feature is computed from observations dated <= ``as_of``.  The helper
never forward-fills through a long gap and never inspects a future row.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date
from math import log, sqrt
from statistics import mean

TRADING_DAYS = 252
MAX_STALE_DAYS = 10

# Canonical GICS-ish sector labels plus common labels already seen in public
# company datasets. Unknown sectors simply have no sector proxy.
SECTOR_ETF = {
    "communication services": "XLC",
    "communications": "XLC",
    "consumer discretionary": "XLY",
    "consumer cyclical": "XLY",
    "consumer staples": "XLP",
    "consumer defensive": "XLP",
    "energy": "XLE",
    "financials": "XLF",
    "financial services": "XLF",
    "health care": "XLV",
    "healthcare": "XLV",
    "industrials": "XLI",
    "information technology": "XLK",
    "technology": "XLK",
    "materials": "XLB",
    "basic materials": "XLB",
    "real estate": "XLRE",
    "utilities": "XLU",
}

REGIME_FEATURE_NAMES = [
    "market_mom_20",
    "market_mom_63",
    "market_mom_126",
    "market_vol_21",
    "market_vol_63",
    "market_drawdown_63",
    "qqq_vs_spy_63",
    "sector_vs_spy_63",
    "breadth_above_63dma",
    "breadth_positive_20d",
    "has_qqq",
    "has_sector",
    "has_breadth",
]


def sector_etf(sector: str | None) -> str | None:
    if not sector:
        return None
    return SECTOR_ETF.get(sector.strip().lower())


def _normalize(rows: list[dict]) -> tuple[list[str], list[float]]:
    clean: dict[str, float] = {}
    for row in rows or []:
        value = row.get("adj_close") or row.get("close")
        if value is None or float(value) <= 0:
            continue
        clean[str(row["date"])[:10]] = float(value)
    dates = sorted(clean)
    return dates, [clean[d] for d in dates]


def _locate(dates: list[str], as_of: str) -> int:
    i = bisect.bisect_right(dates, as_of) - 1
    if i < 0:
        return -1
    if (date.fromisoformat(as_of) - date.fromisoformat(dates[i])).days > MAX_STALE_DAYS:
        return -1
    return i


def _log_return(values: list[float], end: int, width: int) -> float | None:
    start = end - width
    if start < 0:
        return None
    return log(values[end] / values[start])


def _vol(values: list[float], end: int, width: int) -> float | None:
    start = end - width
    if start < 0:
        return None
    rets = [log(values[i] / values[i - 1]) for i in range(start + 1, end + 1)]
    if len(rets) < 2:
        return None
    m = mean(rets)
    sd = sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1))
    return sd * sqrt(TRADING_DAYS)


def _drawdown(values: list[float], end: int, width: int) -> float | None:
    start = max(0, end - width + 1)
    window = values[start:end + 1]
    if not window:
        return None
    peak = max(window)
    return values[end] / peak - 1.0 if peak else None


@dataclass
class _Series:
    dates: list[str]
    values: list[float]

    @classmethod
    def from_rows(cls, rows: list[dict] | None) -> "_Series":
        d, v = _normalize(rows or [])
        return cls(d, v)

    def index(self, as_of: str) -> int:
        return _locate(self.dates, as_of)


@dataclass
class RegimeFeatureProvider:
    """Reusable point-in-time regime feature provider with an as-of cache."""

    spy_rows: list[dict]
    qqq_rows: list[dict] | None = None
    sector_rows: list[dict] | None = None
    universe_rows: dict[str, list[dict]] | None = None
    _cache: dict[str, dict] = field(default_factory=dict, init=False)

    def __post_init__(self):
        self.spy = _Series.from_rows(self.spy_rows)
        self.qqq = _Series.from_rows(self.qqq_rows)
        self.sector = _Series.from_rows(self.sector_rows)
        self.universe = {
            ticker: _Series.from_rows(rows)
            for ticker, rows in (self.universe_rows or {}).items()
        }

    def _breadth(self, as_of: str) -> tuple[float, float, int]:
        above, positive, eligible = 0, 0, 0
        for series in self.universe.values():
            i = series.index(as_of)
            if i < 63:
                continue
            eligible += 1
            sma63 = mean(series.values[i - 62:i + 1])
            above += series.values[i] > sma63
            positive += series.values[i] > series.values[i - 20]
        if not eligible:
            return 0.0, 0.0, 0
        return above / eligible, positive / eligible, eligible

    def features_as_of(self, as_of: str) -> dict:
        if as_of in self._cache:
            return self._cache[as_of]

        si = self.spy.index(as_of)
        if si < 126:
            result = {
                "status": "INSUFFICIENT_MARKET_HISTORY",
                "as_of": as_of,
                "vector": [0.0] * len(REGIME_FEATURE_NAMES),
                "classification": "UNKNOWN",
                "breadth_n": 0,
            }
            self._cache[as_of] = result
            return result

        mom20 = _log_return(self.spy.values, si, 20) or 0.0
        mom63 = _log_return(self.spy.values, si, 63) or 0.0
        mom126 = _log_return(self.spy.values, si, 126) or 0.0
        vol21 = _vol(self.spy.values, si, 21) or 0.0
        vol63 = _vol(self.spy.values, si, 63) or 0.0
        dd63 = _drawdown(self.spy.values, si, 63) or 0.0

        qi = self.qqq.index(as_of)
        has_qqq = float(qi >= 63)
        qqq_rel = 0.0
        if has_qqq:
            qqq_ret = _log_return(self.qqq.values, qi, 63) or 0.0
            qqq_rel = qqq_ret - mom63

        xi = self.sector.index(as_of)
        has_sector = float(xi >= 63)
        sector_rel = 0.0
        if has_sector:
            sector_ret = _log_return(self.sector.values, xi, 63) or 0.0
            sector_rel = sector_ret - mom63

        breadth_above, breadth_positive, breadth_n = self._breadth(as_of)
        has_breadth = float(breadth_n >= 8)
        if not has_breadth:
            breadth_above = breadth_positive = 0.0

        vector = [
            mom20, mom63, mom126, vol21, vol63, dd63,
            qqq_rel, sector_rel, breadth_above, breadth_positive,
            has_qqq, has_sector, has_breadth,
        ]

        # Descriptive regime label only. It is not a probability and never
        # overrides the learned forecast. Missing inputs contribute no vote.
        votes = [mom63 > 0, mom126 > 0]
        if has_qqq:
            votes.append(qqq_rel > 0)
        if has_breadth:
            votes.extend([breadth_above >= 0.55, breadth_positive >= 0.55])
        if vol63 > 0:
            votes.append(vol21 <= vol63 * 1.20)
        score = sum(votes) / len(votes) if votes else 0.5
        classification = "RISK_ON" if score >= 0.67 else "RISK_OFF" if score <= 0.33 else "MIXED"

        result = {
            "status": "OK",
            "as_of": as_of,
            "classification": classification,
            "risk_on_vote_share": round(score, 3),
            "breadth_n": breadth_n,
            "vector": vector,
            "features": dict(zip(REGIME_FEATURE_NAMES, vector)),
        }
        self._cache[as_of] = result
        return result


def empty_regime_vector() -> list[float]:
    return [0.0] * len(REGIME_FEATURE_NAMES)
