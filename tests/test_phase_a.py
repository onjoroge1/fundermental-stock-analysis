import math

from stock_machine.baserates import _bucket, _terciles, compute_base_rates
from stock_machine.valuation_tools import (calculate_dcf,
                                           implied_growth_from_price)


def test_reverse_dcf_recovers_known_growth():
    """Forward-price a DCF at a known growth rate, then invert it."""
    fcf, nd, shares = 100.0, 50.0, 10.0
    fwd = calculate_dcf(fcf, [12.0] * 5, terminal_growth_pct=2.5,
                        discount_rate_pct=9.0, net_debt=nd,
                        diluted_shares=shares)
    market_cap = fwd["equity_value"]
    out = implied_growth_from_price(market_cap, nd, fcf)
    assert math.isclose(out["implied_cagr_pct"], 12.0, abs_tol=0.05)


def test_reverse_dcf_refuses_negative_base():
    assert implied_growth_from_price(1e12, 0, -5e9) is None
    assert implied_growth_from_price(1e12, 0, None) is None


def test_reverse_dcf_flags_unsolvable():
    # absurd price vs tiny cash flow → >150%/yr required
    out = implied_growth_from_price(1e14, 0, 1e6)
    assert out["implied_cagr_pct"] is None and "not solvable" in out["note"]


def _panel(n_per_bucket=40):
    """Synthetic panel: high-growth bucket outperforms, low underperforms."""
    panel = []
    for d in ("2020-01-01", "2021-01-01"):
        for i in range(n_per_bucket):
            for growth, ret in ((5.0, -10.0), (15.0, 0.0), (30.0, 10.0)):
                panel.append({
                    "as_of": d, "ticker": f"T{i}",
                    "factors": {"revenue_yoy_pct": growth + i * 0.01,
                                "earnings_yield_pct": 3.0,
                                "roic_pct": 10.0},
                    "forward": {"fwd_12m_pct": ret + i * 0.01},
                })
    return panel


def test_base_rates_finds_analogs():
    panel = _panel()
    out = compute_base_rates(panel, {"revenue_yoy_pct": 30.2,
                                     "earnings_yield_pct": 3.0,
                                     "roic_pct": 10.0})
    assert out["status"] == "OK"
    assert out["subject_buckets"]["revenue_yoy_pct"] == "high"
    assert out["n_analogs"] >= 30
    assert out["outperform_share"] > 0.9  # high-growth bucket wins by design


def test_base_rates_abstains_without_data():
    out = compute_base_rates(_panel(), {"revenue_yoy_pct": None,
                                        "earnings_yield_pct": 3.0,
                                        "roic_pct": 10.0})
    assert out["status"] == "INSUFFICIENT_DATA"
    assert compute_base_rates([], {})["status"] == "NO_PANEL"


def test_terciles_and_bucket():
    cuts = _terciles(list(map(float, range(30))))
    assert _bucket(0.0, cuts) == "low"
    assert _bucket(15.0, cuts) == "mid"
    assert _bucket(29.0, cuts) == "high"
    assert _bucket(None, cuts) is None
