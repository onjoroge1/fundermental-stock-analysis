"""Versioned PAPER/SHADOW reward contract for Agent Intelligence v2."""

from __future__ import annotations

from math import isfinite, sqrt

VERSION = "risk-scaled-net-paper-reward.v4"
TRADING_DAYS = 252
# Floor keeps an implausibly calm ex-ante estimate from inflating rewards.
MIN_ANNUAL_VOL = 0.05
# Documented fallback when the decision state lacks a volatility estimate.
DEFAULT_ANNUAL_VOL = 0.40


def _valid(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
    )


def risk_scale(technical_features: dict | None, holding_sessions: int) -> dict:
    """Ex-ante volatility over the holding period, in percentage points.

    Uses only the decision-time state, never the realized outcome path.
    """
    if isinstance(holding_sessions, bool) or not isinstance(holding_sessions, int) \
            or holding_sessions < 1:
        raise ValueError("REWARD_INPUT_INVALID")
    features = technical_features or {}
    basis, annual = "DEFAULT_ANNUAL_VOL", DEFAULT_ANNUAL_VOL
    for name in ("realized_vol_20", "realized_vol_60"):
        value = features.get(name)
        if _valid(value) and value > 0:
            basis, annual = name, float(value)
            break
    annual = max(annual, MIN_ANNUAL_VOL)
    return {
        "risk_scale_pct": round(annual * sqrt(holding_sessions / TRADING_DAYS) * 100, 6),
        "annual_vol": annual,
        "basis": basis,
        "holding_sessions": holding_sessions,
    }


def compute(
    *,
    gross_return_pct: float,
    max_drawdown_pct: float,
    capital_used_pct: float,
    turnover_pct: float,
    costs_pct: float,
    risk_scale_pct: float,
) -> dict:
    """Net return after costs, in units of ex-ante holding-period volatility.

    gross_return_pct and costs_pct are percentages of position notional;
    costs are subtracted exactly once. Abstention (zero return, zero cost)
    scores zero. Drawdown, capital and turnover are recorded as diagnostics
    only: v3 penalized them per trade, which made any realistic positive edge
    score below abstention. Risk is controlled by position sizing instead.
    """
    values = (
        gross_return_pct,
        max_drawdown_pct,
        capital_used_pct,
        turnover_pct,
        costs_pct,
        risk_scale_pct,
    )
    if any(not _valid(v) for v in values):
        raise ValueError("REWARD_INPUT_INVALID")
    if (
        max_drawdown_pct > 0
        or not 0 <= capital_used_pct <= 100
        or min(turnover_pct, costs_pct) < 0
        or risk_scale_pct <= 0
    ):
        raise ValueError("REWARD_INPUT_INVALID")
    net_return_pct = float(gross_return_pct) - float(costs_pct)
    reward = net_return_pct / float(risk_scale_pct)
    return {
        "schema_version": VERSION,
        "reward": round(reward, 6),
        "components": {
            "gross_return_pct": gross_return_pct,
            "cost_pct": float(costs_pct),
            "net_return_pct": round(net_return_pct, 6),
            "risk_scale_pct": float(risk_scale_pct),
        },
        "diagnostics": {
            "max_drawdown_pct": max_drawdown_pct,
            "capital_used_pct": capital_used_pct,
            "turnover_pct": turnover_pct,
        },
        "meaning": "experimental PAPER/SHADOW utility; not a financial-performance guarantee",
    }
