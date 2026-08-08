"""Deterministic valuation calculators exposed to Claude via MCP. Claude
supplies assumptions; the arithmetic happens here, never in the model."""
from __future__ import annotations


def calculate_dcf(fcf_base: float, growth_rates_pct: list[float],
                  terminal_growth_pct: float, discount_rate_pct: float,
                  net_debt: float, diluted_shares: float) -> dict:
    """Standard FCF DCF. growth_rates_pct is one entry per explicit year."""
    if diluted_shares <= 0:
        raise ValueError("diluted_shares must be positive")
    r = discount_rate_pct / 100
    g_term = terminal_growth_pct / 100
    if r <= g_term:
        raise ValueError("discount rate must exceed terminal growth")

    fcf = fcf_base
    pv_explicit = 0.0
    projections = []
    for year, g in enumerate(growth_rates_pct, start=1):
        fcf *= 1 + g / 100
        pv = fcf / (1 + r) ** year
        pv_explicit += pv
        projections.append({"year": year, "fcf": round(fcf, 0),
                            "present_value": round(pv, 0)})
    terminal_value = fcf * (1 + g_term) / (r - g_term)
    pv_terminal = terminal_value / (1 + r) ** len(growth_rates_pct)
    ev = pv_explicit + pv_terminal
    equity_value = ev - net_debt
    return {
        "enterprise_value": round(ev, 0),
        "pv_explicit_fcf": round(pv_explicit, 0),
        "pv_terminal_value": round(pv_terminal, 0),
        "terminal_value_share_of_ev_pct": round(pv_terminal / ev * 100, 1),
        "equity_value": round(equity_value, 0),
        "fair_value_per_share": round(equity_value / diluted_shares, 2),
        "projections": projections,
        "assumptions": {
            "fcf_base": fcf_base, "growth_rates_pct": growth_rates_pct,
            "terminal_growth_pct": terminal_growth_pct,
            "discount_rate_pct": discount_rate_pct,
            "net_debt": net_debt, "diluted_shares": diluted_shares,
        },
    }


def implied_growth_from_price(market_cap: float, net_debt: float,
                              cash_flow_base: float,
                              discount_rate_pct: float = 9.0,
                              terminal_growth_pct: float = 2.5,
                              years: int = 5) -> dict | None:
    """Reverse DCF: solve for the constant cash-flow growth rate the current
    price requires. Answers 'what must happen to justify today's price'
    instead of 'what is it worth'. Returns None when the base cash flow is
    non-positive (growth is then undefined — flag, don't guess)."""
    if cash_flow_base is None or cash_flow_base <= 0 or market_cap is None:
        return None
    ev = market_cap + (net_debt or 0)
    r = discount_rate_pct / 100
    tg = terminal_growth_pct / 100

    def pv(g: float) -> float:
        cf, total = cash_flow_base, 0.0
        for y in range(1, years + 1):
            cf *= 1 + g
            total += cf / (1 + r) ** y
        total += (cf * (1 + tg) / (r - tg)) / (1 + r) ** years
        return total

    lo, hi = -0.50, 1.50
    if pv(hi) < ev:
        return {"implied_cagr_pct": None, "note": "price requires >150%/yr "
                "growth under these assumptions — not solvable"}
    if pv(lo) > ev:
        return {"implied_cagr_pct": None, "note": "price is below even a "
                "-50%/yr decline scenario — not solvable"}
    for _ in range(80):
        mid = (lo + hi) / 2
        if pv(mid) < ev:
            lo = mid
        else:
            hi = mid
    return {
        "implied_cagr_pct": round((lo + hi) / 2 * 100, 2),
        "assumptions": {
            "discount_rate_pct": discount_rate_pct,
            "terminal_growth_pct": terminal_growth_pct,
            "explicit_years": years,
            "note": "documented conventions, not fitted parameters",
        },
    }


def calculate_multiple_valuation(metric_value: float, multiple: float,
                                 net_adjustment: float = 0.0,
                                 diluted_shares: float | None = None) -> dict:
    """value = metric * multiple + net_adjustment (e.g. EPS×P/E, or
    EV/FCF×FCF minus net debt via net_adjustment)."""
    value = metric_value * multiple + net_adjustment
    out = {"implied_value": round(value, 2)}
    if diluted_shares:
        out["implied_value_per_share"] = round(value / diluted_shares, 2)
    return out


def calculate_scenario_values(scenarios: list[dict]) -> dict:
    """Each scenario: {name, probability, eps, valuation_multiple} (or a
    precomputed fair_value). Probabilities must total 1.00."""
    total_p = sum(s["probability"] for s in scenarios)
    if abs(total_p - 1.0) > 1e-6:
        raise ValueError(f"scenario probabilities sum to {total_p}, not 1.0")
    out = []
    expected = 0.0
    for s in scenarios:
        fv = s.get("fair_value")
        if fv is None:
            fv = s["eps"] * s["valuation_multiple"]
        expected += s["probability"] * fv
        out.append({**s, "fair_value": round(fv, 2)})
    return {"scenarios": out, "probability_weighted_value": round(expected, 2)}


def calculate_expected_return(current_price: float,
                              outcomes: list[dict]) -> dict:
    """outcomes: [{probability, price}] — probabilities must total 1.00."""
    if current_price <= 0:
        raise ValueError("current_price must be positive")
    total_p = sum(o["probability"] for o in outcomes)
    if abs(total_p - 1.0) > 1e-6:
        raise ValueError(f"outcome probabilities sum to {total_p}, not 1.0")
    expected_price = sum(o["probability"] * o["price"] for o in outcomes)
    prob_positive = sum(o["probability"] for o in outcomes
                        if o["price"] > current_price)
    return {
        "expected_price": round(expected_price, 2),
        "expected_return_pct": round((expected_price / current_price - 1) * 100, 2),
        "probability_of_positive_return": round(prob_positive, 4),
        "downside_price": round(min(o["price"] for o in outcomes), 2),
        "upside_price": round(max(o["price"] for o in outcomes), 2),
    }
