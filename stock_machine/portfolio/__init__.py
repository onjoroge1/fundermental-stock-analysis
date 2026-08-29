"""Risk-aware portfolio and trade-expression proposal layer.

This package never places orders. It converts stored research forecasts into
reviewable target weights and then compares stock with supported option
expressions under explicit risk/liquidity gates.
"""

from .constructor import PortfolioPolicy, build_proposal
from .expression import ExpressionPolicy, ExpressionType, select_expression

__all__ = [
    "PortfolioPolicy",
    "build_proposal",
    "ExpressionPolicy",
    "ExpressionType",
    "select_expression",
]
