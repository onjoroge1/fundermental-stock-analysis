from __future__ import annotations

from datetime import date, timedelta
from math import exp

from stock_machine.portfolio import PortfolioPolicy, build_proposal
from stock_machine.portfolio.risk import beta, correlation, realized_vol


def _prices(daily, n=320, start="2025-01-01"):
    d0 = date.fromisoformat(start)
    px = 100.0
    rows = []
    for i in range(n):
        px *= exp(daily + (0.0003 if i % 7 == 0 else -0.0001))
        rows.append({"date": (d0 + timedelta(days=i)).isoformat(), "adj_close": px})
    return rows


def _forecast(expected, prob, horizon=63):
    return {"alpha_forecast": {"status": "OK", "horizons": {
        str(horizon): {"status": "OK", "expected_excess_return_pct": expected,
                       "prob_outperform": prob}
    }}}


def test_risk_estimators_return_finite_values():
    spy = _prices(0.0004)
    stock = _prices(0.0007)
    assert realized_vol(stock) is not None
    assert beta(stock, spy) is not None
    assert correlation(stock, spy) is not None


def test_portfolio_obeys_hard_exposure_limits():
    spy = _prices(0.0004)
    candidates = []
    sectors = ["Tech", "Tech", "Health", "Energy", "Finance", "Industrial"]
    for i, sector in enumerate(sectors):
        candidates.append({
            "ticker": f"T{i}", "sector": sector,
            "forecast": _forecast(8 - i * 0.5, 0.68 - i * 0.01),
            "price_rows": _prices(0.0005 + i * 0.00005),
        })
    policy = PortfolioPolicy(gross_limit=0.60, net_limit=0.40,
                             single_name_limit=0.12, sector_limit=0.20,
                             beta_limit=0.40, max_pair_correlation=0.9999)
    result = build_proposal(candidates, spy, policy)
    assert result["proposal_only"] is True
    assert result["exposures"]["gross"] <= 0.600001
    assert abs(result["exposures"]["net"]) <= 0.400001
    assert abs(result["exposures"]["beta"]) <= 0.400001
    assert all(abs(p["weight"]) <= 0.120001 for p in result["positions"])
    assert all(v <= 0.200001 for v in result["exposures"]["sector_abs"].values())


def test_weak_or_conflicted_signals_abstain():
    spy = _prices(0.0004)
    candidates = [
        {"ticker": "WEAK", "sector": "Tech", "forecast": _forecast(0.5, 0.52),
         "price_rows": _prices(0.0005)},
        {"ticker": "CONFLICT", "sector": "Health", "forecast": _forecast(-5.0, 0.70),
         "price_rows": _prices(0.0002)},
    ]
    result = build_proposal(candidates, spy)
    assert result["positions"] == []
