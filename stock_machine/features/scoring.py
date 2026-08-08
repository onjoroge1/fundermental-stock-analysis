"""Fundamental scoring engine — computed by code, never improvised by Claude.

Each metric maps to 0–100 through explicit piecewise-linear breakpoints.
BASE_THRESHOLDS is the general-company default; SECTOR_OVERRIDES re-anchor
individual metrics to the economics of each sector (an 18% gross margin is a
failing grade for software and a solid one for an automaker). Every score
output names the profile applied and which metrics it re-anchored — the
thresholds are hand-set analyst conventions (documented judgment, not fitted
parameters) and stay uncalibrated until the backtest exists.

Components with no underlying data score None and their weight renormalizes —
a missing dataset must not silently read as average."""
from __future__ import annotations

DEFAULT_WEIGHTS = {
    "growth": 0.15,
    "profitability": 0.15,
    "earnings_quality": 0.15,
    "financial_health": 0.10,
    "capital_allocation": 0.10,
    "expectations": 0.15,
    "valuation": 0.20,
}

# (value, score) breakpoints, ascending by value
BASE_THRESHOLDS: dict[str, list[tuple[float, float]]] = {
    "revenue_yoy_pct": [(-10, 0), (0, 35), (10, 65), (25, 90), (40, 100)],
    "eps_yoy_pct": [(-20, 0), (0, 35), (15, 70), (35, 100)],
    "revenue_cagr_3y_pct": [(-5, 0), (0, 30), (10, 65), (20, 90), (30, 100)],
    "gross_margin_pct": [(10, 10), (30, 40), (50, 70), (70, 95), (80, 100)],
    "operating_margin_pct": [(-5, 0), (5, 30), (15, 60), (30, 90), (40, 100)],
    "roic_pct": [(0, 10), (8, 45), (15, 70), (30, 95), (50, 100)],
    "fcf_margin_pct": [(-5, 0), (5, 35), (15, 65), (30, 95)],
    "ocf_to_net_income": [(0.5, 10), (0.9, 50), (1.1, 80), (1.4, 100)],
    "accrual_ratio": [(-10, 100), (0, 70), (5, 40), (15, 0)],
    "receivables_gap": [(-10, 100), (0, 75), (10, 40), (25, 0)],
    "sbc_to_revenue": [(0, 100), (3, 80), (8, 50), (15, 20), (25, 0)],
    "current_ratio": [(0.5, 0), (1.0, 45), (1.5, 75), (2.5, 100)],
    "net_debt_to_oi": [(-2, 100), (0, 85), (1.5, 60), (3, 30), (5, 0)],
    "interest_coverage": [(1, 0), (3, 40), (8, 75), (20, 100)],
    "shareholder_yield": [(-2, 10), (0, 40), (3, 75), (6, 100)],
    "share_change_yoy": [(-4, 100), (0, 70), (3, 30), (8, 0)],
    "fcf_yield": [(0.5, 5), (2, 30), (4, 60), (7, 90), (10, 100)],
    "earnings_yield": [(0.5, 5), (2, 30), (5, 65), (8, 95)],
    "pe_5y_percentile": [(5, 100), (30, 75), (50, 55), (80, 25), (95, 5)],
    "avg_surprise_pct": [(-10, 0), (0, 40), (3, 65), (8, 90), (15, 100)],
    "beat_rate_pct": [(25, 10), (50, 45), (75, 80), (100, 100)],
}

# Threshold keys that exist only in specific sector profiles (scored only
# when the profile provides them):
PROFILE_EXTENSION_METRICS = {"roe_pct"}

# Sector re-anchors: only metrics whose economics genuinely differ by sector.
SECTOR_OVERRIDES: dict[str, dict[str, list[tuple[float, float]]]] = {
    "Software & Internet": {
        "revenue_yoy_pct": [(-5, 0), (0, 25), (10, 55), (20, 80), (35, 100)],
        "gross_margin_pct": [(30, 10), (50, 35), (65, 60), (75, 85), (85, 100)],
        "operating_margin_pct": [(0, 0), (10, 30), (20, 55), (30, 80), (40, 100)],
        "fcf_margin_pct": [(0, 0), (10, 35), (20, 65), (30, 90), (40, 100)],
        "sbc_to_revenue": [(0, 100), (5, 80), (10, 50), (20, 15), (30, 0)],
        "fcf_yield": [(0.5, 5), (2, 35), (3.5, 60), (6, 90), (9, 100)],
    },
    "Semiconductors": {
        "revenue_yoy_pct": [(-15, 0), (0, 30), (15, 60), (30, 85), (50, 100)],
        "gross_margin_pct": [(20, 5), (35, 30), (50, 60), (65, 90), (75, 100)],
        "operating_margin_pct": [(-5, 0), (10, 35), (25, 65), (40, 90), (50, 100)],
    },
    "Technology Hardware": {
        "gross_margin_pct": [(15, 10), (30, 40), (45, 70), (60, 95)],
        "operating_margin_pct": [(0, 0), (8, 35), (18, 65), (30, 95)],
    },
    "Consumer & Retail": {
        "revenue_yoy_pct": [(-8, 0), (0, 30), (5, 60), (12, 85), (20, 100)],
        "revenue_cagr_3y_pct": [(-5, 0), (0, 35), (5, 65), (10, 90), (15, 100)],
        "gross_margin_pct": [(15, 10), (25, 40), (35, 65), (45, 85), (55, 100)],
        "operating_margin_pct": [(0, 0), (4, 35), (8, 60), (15, 85), (25, 100)],
        "fcf_margin_pct": [(-2, 0), (2, 35), (6, 65), (10, 90), (15, 100)],
        "current_ratio": [(0.5, 0), (0.9, 45), (1.3, 75), (2.0, 100)],
    },
    "Banks & Consumer Finance": {
        # margins/cash-flow inputs are suppressed in bank mode; ROE carries
        # the profitability read, growth bars sit lower, yields matter more
        "roe_pct": [(0, 0), (5, 30), (10, 60), (15, 85), (20, 100)],
        "revenue_yoy_pct": [(-8, 0), (0, 30), (8, 60), (18, 85), (30, 100)],
        "earnings_yield": [(1, 5), (4, 40), (7, 70), (11, 95)],
        "share_change_yoy": [(-3, 100), (0, 65), (5, 25), (12, 0)],
    },
    "Automobiles": {
        "revenue_yoy_pct": [(-10, 0), (0, 35), (8, 70), (20, 100)],
        "gross_margin_pct": [(5, 5), (12, 40), (20, 70), (30, 95)],
        "operating_margin_pct": [(-2, 0), (3, 35), (7, 65), (12, 90), (18, 100)],
        "fcf_margin_pct": [(-5, 0), (0, 30), (4, 65), (8, 95)],
        "roic_pct": [(0, 10), (5, 45), (10, 75), (20, 100)],
        "fcf_yield": [(2, 10), (5, 40), (8, 70), (12, 95)],
        "earnings_yield": [(2, 5), (6, 45), (10, 80), (15, 100)],
        "current_ratio": [(0.8, 10), (1.0, 45), (1.3, 80), (1.8, 100)],
    },
}


def thresholds_for(sector: str | None) -> tuple[dict, str, list[str]]:
    """Returns (thresholds, profile_name, overridden_metric_keys)."""
    overrides = SECTOR_OVERRIDES.get(sector or "")
    if not overrides:
        return BASE_THRESHOLDS, "general", []
    return {**BASE_THRESHOLDS, **overrides}, sector, sorted(overrides)


def _scale(value, points: list[tuple[float, float]]):
    if value is None:
        return None
    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return None


def _avg(scores: list) -> float | None:
    present = [s for s in scores if s is not None]
    return round(sum(present) / len(present), 1) if present else None


def score_growth(g: dict, t: dict) -> float | None:
    return _avg([
        _scale(g.get("revenue_yoy_pct"), t["revenue_yoy_pct"]),
        _scale(g.get("eps_yoy_pct"), t["eps_yoy_pct"]),
        _scale(g.get("revenue_cagr_3y_pct"), t["revenue_cagr_3y_pct"]),
    ])


def score_profitability(p: dict, t: dict) -> float | None:
    inputs = [
        _scale(p.get("gross_margin_pct"), t["gross_margin_pct"]),
        _scale(p.get("operating_margin_pct"), t["operating_margin_pct"]),
        _scale(p.get("roic_pct"), t["roic_pct"]),
        _scale(p.get("fcf_margin_pct"), t["fcf_margin_pct"]),
    ]
    if "roe_pct" in t:  # bank profile: ROE is the meaningful margin metric
        inputs.append(_scale(p.get("roe_pct"), t["roe_pct"]))
    return _avg(inputs)


def score_earnings_quality(eq: dict, t: dict) -> float | None:
    return _avg([
        _scale(eq.get("operating_cash_flow_to_net_income"), t["ocf_to_net_income"]),
        _scale(eq.get("accrual_ratio_pct_of_assets"), t["accrual_ratio"]),
        _scale(eq.get("receivables_growth_minus_revenue_growth_pct"), t["receivables_gap"]),
        _scale(eq.get("stock_comp_to_revenue_pct"), t["sbc_to_revenue"]),
    ])


def score_financial_health(fh: dict, t: dict) -> float | None:
    return _avg([
        _scale(fh.get("current_ratio"), t["current_ratio"]),
        _scale(fh.get("net_debt_to_operating_income"), t["net_debt_to_oi"]),
        _scale(fh.get("interest_coverage"), t["interest_coverage"]),
    ])


def score_capital_allocation(ca: dict, t: dict) -> float | None:
    return _avg([
        _scale(ca.get("net_shareholder_yield_pct"), t["shareholder_yield"]),
        _scale(ca.get("diluted_share_change_yoy_pct"), t["share_change_yoy"]),
    ])


def score_expectations(surprises: list[dict] | None,
                       t: dict = BASE_THRESHOLDS) -> float | None:
    """From vendor-recorded actual-vs-estimate EPS history: average surprise
    over the last 4 events and beat rate over the last 8. Returns None with
    fewer than 4 events — never a guessed score."""
    if not surprises:
        return None
    with_pct = [s for s in surprises if s.get("surprise_pct") is not None]
    if len(with_pct) < 4:
        return None
    last4 = [s["surprise_pct"] for s in with_pct[-4:]]
    last8 = with_pct[-8:]
    avg_surprise = sum(last4) / len(last4)
    beat_rate = 100 * sum(1 for s in last8 if s["surprise_pct"] > 0) / len(last8)
    return _avg([
        _scale(avg_surprise, t["avg_surprise_pct"]),
        _scale(beat_rate, t["beat_rate_pct"]),
    ])


def score_valuation(v: dict, t: dict) -> float | None:
    return _avg([
        _scale(v.get("fcf_yield_pct"), t["fcf_yield"]),
        _scale(v.get("earnings_yield_pct"), t["earnings_yield"]),
        _scale(v.get("pe_5y_percentile"), t["pe_5y_percentile"]),
    ])


def composite(scores: dict[str, float | None],
              weights: dict[str, float] = DEFAULT_WEIGHTS) -> dict:
    usable = {k: s for k, s in scores.items() if s is not None}
    if not usable:
        return {"composite_score": None, "weights_used": {}, "components": scores}
    total_w = sum(weights[k] for k in usable)
    weights_used = {k: round(weights[k] / total_w, 4) for k in usable}
    comp = round(sum(usable[k] * weights_used[k] for k in usable), 1)
    return {"composite_score": comp, "weights_used": weights_used,
            "components": scores}


def score_all(derived: dict, surprises: list[dict] | None = None,
              sector: str | None = None) -> dict:
    t, profile, overridden = thresholds_for(sector)
    scores = {
        "growth": score_growth(derived.get("growth", {}), t),
        "profitability": score_profitability(derived.get("profitability", {}), t),
        "earnings_quality": score_earnings_quality(derived.get("earnings_quality", {}), t),
        "financial_health": score_financial_health(derived.get("financial_health", {}), t),
        "capital_allocation": score_capital_allocation(derived.get("capital_allocation", {}), t),
        "expectations": score_expectations(surprises, t),
        "valuation": score_valuation(derived.get("valuation", {}), t),
    }
    out = composite(scores)
    out["scoring_profile"] = {
        "profile": profile,
        "sector_adjusted_metrics": overridden,
        "note": "Thresholds are documented analyst conventions, not fitted "
                "parameters; predictive value unproven until backtested.",
    }
    return out
