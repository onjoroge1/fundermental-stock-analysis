"""Versioned PAPER/SHADOW reward contract for Agent Intelligence v2."""
from __future__ import annotations

VERSION="risk-adjusted-paper-reward.v1"


def compute(*, net_return_pct: float, max_drawdown_pct: float,
            capital_used_pct: float, turnover_pct: float,
            costs_pct: float) -> dict:
    drawdown_penalty=max(0.0,-float(max_drawdown_pct))
    reward=(float(net_return_pct)
            -0.60*drawdown_penalty
            -0.10*max(0.0,float(capital_used_pct))
            -0.10*max(0.0,float(turnover_pct))
            -max(0.0,float(costs_pct)))
    return {"schema_version":VERSION,"reward":round(reward,6),
            "components":{"net_return_pct":net_return_pct,
                          "drawdown_penalty":round(.60*drawdown_penalty,6),
                          "capital_penalty":round(.10*max(0.0,float(capital_used_pct)),6),
                          "turnover_penalty":round(.10*max(0.0,float(turnover_pct)),6),
                          "cost_penalty":max(0.0,float(costs_pct))},
            "meaning":"experimental PAPER/SHADOW utility; not a financial-performance guarantee"}
