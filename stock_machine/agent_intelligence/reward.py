"""Versioned PAPER/SHADOW reward contract for Agent Intelligence v2."""
from __future__ import annotations

VERSION="risk-adjusted-paper-reward.v2"


def compute(*, gross_return_pct: float, max_drawdown_pct: float,
            capital_used_pct: float, turnover_pct: float,
            costs_pct: float) -> dict:
    """Risk-adjusted experimental utility in percentage-point units.

    gross_return_pct is before explicit costs; costs are subtracted exactly
    once here. All penalties are operating conventions to be validated, not
    claims of optimal utility.
    """
    drawdown_penalty=max(0.0,-float(max_drawdown_pct))
    reward=(float(gross_return_pct)
            -0.60*drawdown_penalty
            -0.10*max(0.0,float(capital_used_pct))
            -0.10*max(0.0,float(turnover_pct))
            -max(0.0,float(costs_pct)))
    return {"schema_version":VERSION,"reward":round(reward,6),
            "components":{"gross_return_pct":gross_return_pct,
                          "drawdown_penalty":round(.60*drawdown_penalty,6),
                          "capital_penalty":round(.10*max(0.0,float(capital_used_pct)),6),
                          "turnover_penalty":round(.10*max(0.0,float(turnover_pct)),6),
                          "cost_penalty":max(0.0,float(costs_pct))},
            "meaning":"experimental PAPER/SHADOW utility; not a financial-performance guarantee"}
