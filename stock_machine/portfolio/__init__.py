"""Risk-aware portfolio proposal layer.

This package never places orders. It converts stored research forecasts into
reviewable target-weight proposals under explicit portfolio constraints.
"""

from .constructor import PortfolioPolicy, build_proposal

__all__ = ["PortfolioPolicy", "build_proposal"]
