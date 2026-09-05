"""Deterministic derived metrics. Pure functions over normalized period dicts
(ascending by period_end). Claude reads these values; it never recalculates.

All functions are None-safe: missing inputs yield None, never a guess."""
from __future__ import annotations

FLOW_SUM_FIELDS = [
    "revenue", "cost_of_revenue", "gross_profit", "research_and_development",
    "selling_general_and_administrative", "operating_income", "pretax_income",
    "income_tax", "net_income", "operating_cash_flow", "capital_expenditures",
    "free_cash_flow", "share_repurchases", "dividends_paid",
    "stock_based_compensation", "acquisitions", "basic_eps", "diluted_eps",
    "net_interest_income", "noninterest_income", "noninterest_expense",
    "provision_for_credit_losses",
]


def _f(period: dict | None, field: str):
    if not period:
        return None
    return period.get("fields", {}).get(field)


def _div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def _pct(a, b):
    """Percent change a vs b, guarded for sign flips through zero/negative base."""
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / abs(b) * 100, 2)


def _r(x, nd=2):
    return None if x is None else round(x, nd)


def build_ttm(quarters: list[dict]) -> dict | None:
    """Trailing-12-month synthetic period from the last 4 quarters. Requires 4
    contiguous quarters (each start within ~14 days of prior end)."""
    if len(quarters) < 4:
        return None
    last4 = quarters[-4:]
    # enforce contiguity: each quarter must start within ~21 days of the prior
    # end, else the "TTM" silently spans a gap (fiscal-year changes, missing
    # filings) and every trailing metric built on it is wrong
    from datetime import date
    for prev, cur in zip(last4, last4[1:]):
        start = cur.get("period_start")
        if not start:
            return None
        gap = (date.fromisoformat(start)
               - date.fromisoformat(prev["period_end"])).days
        if not -7 <= gap <= 21:
            return None
    fields: dict = {}
    for f in FLOW_SUM_FIELDS:
        vals = [_f(q, f) for q in last4]
        if all(v is not None for v in vals):
            fields[f] = sum(vals)
    # balance sheet + share counts: latest quarter
    latest = last4[-1]
    for f, v in latest.get("fields", {}).items():
        if f not in fields and f not in FLOW_SUM_FIELDS:
            fields[f] = v
    # derived Q4 quarters carry no share counts — use the newest quarter that does
    for f in ("weighted_average_basic_shares", "weighted_average_diluted_shares"):
        for q in reversed(last4):
            if _f(q, f) is not None:
                fields[f] = _f(q, f)
                break
    # derived Q4 periods carry no EPS (non-additive), so a summed TTM EPS is
    # often unavailable — fall back to TTM net income / latest diluted shares
    if "diluted_eps" not in fields:
        ni, sh = fields.get("net_income"), fields.get("weighted_average_diluted_shares")
        if ni is not None and sh:
            eps = round(ni / sh, 4)
            # scale guard: an EPS beyond ±10,000 means the share count is
            # mis-scaled — leave EPS missing rather than store a wrong value
            if abs(eps) <= 10_000:
                fields["diluted_eps"] = eps
    return {
        "period_end": latest["period_end"],
        "period_start": last4[0].get("period_start"),
        "duration_type": "ttm", "fields": fields,
        "available_at": max(q.get("available_at") or "" for q in last4) or None,
        "source_periods": [q["period_end"] for q in last4],
    }


def total_debt(period: dict) -> float | None:
    parts = [_f(period, "short_term_debt"), _f(period, "commercial_paper"),
             _f(period, "long_term_debt")]
    present = [p for p in parts if p is not None]
    return sum(present) if present else None


def net_debt(period: dict) -> float | None:
    debt = total_debt(period)
    cash_parts = [_f(period, "cash_and_equivalents"),
                  _f(period, "marketable_securities_current")]
    cash = sum(p for p in cash_parts if p is not None) if any(
        p is not None for p in cash_parts) else None
    if debt is None and cash is None:
        return None
    return (debt or 0) - (cash or 0)


def growth_metrics(quarters: list[dict], annuals: list[dict]) -> dict:
    q = quarters[-1] if quarters else None
    q_yoy = quarters[-5] if len(quarters) >= 5 else None
    q_prior = quarters[-2] if len(quarters) >= 2 else None
    a = annuals[-1] if annuals else None
    a3 = annuals[-4] if len(annuals) >= 4 else None
    cagr = None
    if a and a3:
        r0, r1 = _f(a3, "revenue"), _f(a, "revenue")
        if r0 and r1 and r0 > 0 and r1 > 0:
            cagr = round(((r1 / r0) ** (1 / 3) - 1) * 100, 2)
    return {
        "revenue_yoy_pct": _pct(_f(q, "revenue"), _f(q_yoy, "revenue")),
        "revenue_qoq_pct": _pct(_f(q, "revenue"), _f(q_prior, "revenue")),
        "revenue_cagr_3y_pct": cagr,
        "eps_yoy_pct": _pct(_f(q, "diluted_eps"), _f(q_yoy, "diluted_eps")),
        "operating_income_yoy_pct": _pct(_f(q, "operating_income"),
                                         _f(q_yoy, "operating_income")),
        "fcf_yoy_pct": _pct(_f(q, "free_cash_flow"), _f(q_yoy, "free_cash_flow")),
    }


def profitability_metrics(ttm: dict | None, prior_ttm: dict | None = None) -> dict:
    rev = _f(ttm, "revenue")
    out = {
        "gross_margin_pct": _r(_div(_f(ttm, "gross_profit"), rev) and
                               _div(_f(ttm, "gross_profit"), rev) * 100),
        "operating_margin_pct": _r((_div(_f(ttm, "operating_income"), rev) or 0) * 100
                                   if _div(_f(ttm, "operating_income"), rev) is not None else None),
        "net_margin_pct": _r((_div(_f(ttm, "net_income"), rev) or 0) * 100
                             if _div(_f(ttm, "net_income"), rev) is not None else None),
        "fcf_margin_pct": _r((_div(_f(ttm, "free_cash_flow"), rev) or 0) * 100
                             if _div(_f(ttm, "free_cash_flow"), rev) is not None else None),
        "roe_pct": _r((_div(_f(ttm, "net_income"), _f(ttm, "shareholders_equity")) or 0) * 100
                      if _div(_f(ttm, "net_income"), _f(ttm, "shareholders_equity")) is not None else None),
        "incremental_operating_margin_pct": None,
    }
    if prior_ttm:
        d_rev = (rev - _f(prior_ttm, "revenue")) if rev is not None and _f(prior_ttm, "revenue") is not None else None
        d_oi = ((_f(ttm, "operating_income") - _f(prior_ttm, "operating_income"))
                if _f(ttm, "operating_income") is not None and _f(prior_ttm, "operating_income") is not None else None)
        if d_rev not in (None, 0) and d_oi is not None:
            out["incremental_operating_margin_pct"] = _r(d_oi / d_rev * 100)
    # ROIC ≈ NOPAT / (equity + net debt)
    oi, tax, ptx = _f(ttm, "operating_income"), _f(ttm, "income_tax"), _f(ttm, "pretax_income")
    eq = _f(ttm, "shareholders_equity")
    nd = net_debt(ttm) if ttm else None
    if None not in (oi, tax, ptx, eq) and ptx not in (0,) and nd is not None:
        tax_rate = max(0.0, min(0.5, tax / ptx))
        invested = eq + max(nd, 0)
        if invested > 0:
            out["roic_pct"] = _r(oi * (1 - tax_rate) / invested * 100)
        else:
            out["roic_pct"] = None
    else:
        out["roic_pct"] = None
    return out


def earnings_quality_metrics(ttm: dict | None, quarters: list[dict]) -> dict:
    ni = _f(ttm, "net_income")
    q = quarters[-1] if quarters else None
    q_yoy = quarters[-5] if len(quarters) >= 5 else None
    rec_gap = None
    inv_gap = None
    rev_g = _pct(_f(q, "revenue"), _f(q_yoy, "revenue"))
    rec_g = _pct(_f(q, "accounts_receivable"), _f(q_yoy, "accounts_receivable"))
    inv_g = _pct(_f(q, "inventory"), _f(q_yoy, "inventory"))
    if rev_g is not None and rec_g is not None:
        rec_gap = _r(rec_g - rev_g)
    if rev_g is not None and inv_g is not None:
        inv_gap = _r(inv_g - rev_g)
    ocf, fcf = _f(ttm, "operating_cash_flow"), _f(ttm, "free_cash_flow")
    accrual = None
    ta = _f(ttm, "total_assets")
    if None not in (ni, ocf, ta) and ta:
        accrual = _r((ni - ocf) / ta * 100)
    return {
        "operating_cash_flow_to_net_income": _r(_div(ocf, ni)),
        "fcf_to_net_income": _r(_div(fcf, ni)),
        "accrual_ratio_pct_of_assets": accrual,
        "receivables_growth_minus_revenue_growth_pct": rec_gap,
        "inventory_growth_minus_revenue_growth_pct": inv_gap,
        "stock_comp_to_revenue_pct": _r((_div(_f(ttm, "stock_based_compensation"), _f(ttm, "revenue")) or 0) * 100
                                        if _div(_f(ttm, "stock_based_compensation"), _f(ttm, "revenue")) is not None else None),
    }


def financial_health_metrics(latest_q: dict | None, ttm: dict | None) -> dict:
    ca = _f(latest_q, "current_assets")
    cl = _f(latest_q, "current_liabilities")
    inv = _f(latest_q, "inventory")
    quick = None
    if ca is not None and cl not in (None, 0):
        quick = _r((ca - (inv or 0)) / cl)
    oi = _f(ttm, "operating_income")
    ie = _f(ttm, "interest_expense")
    ebitda_proxy = oi  # D&A not mapped yet; documented as operating-income proxy
    nd = net_debt(latest_q) if latest_q else None
    return {
        "current_ratio": _r(_div(ca, cl)),
        "quick_ratio_ex_inventory": quick,
        "total_debt": total_debt(latest_q) if latest_q else None,
        "net_debt": nd,
        "net_debt_to_operating_income": _r(_div(nd, ebitda_proxy)),
        "interest_coverage": _r(_div(oi, ie)),
    }


def capital_allocation_metrics(ttm: dict | None, quarters: list[dict],
                               market_cap: float | None) -> dict:
    buyback = _f(ttm, "share_repurchases")
    divs = _f(ttm, "dividends_paid")
    q = quarters[-1] if quarters else None
    q_yoy = quarters[-5] if len(quarters) >= 5 else None
    return {
        "buyback_yield_pct": _r((_div(buyback, market_cap) or 0) * 100
                                if _div(buyback, market_cap) is not None else None),
        "dividend_yield_pct": _r((_div(divs, market_cap) or 0) * 100
                                 if _div(divs, market_cap) is not None else None),
        "net_shareholder_yield_pct": _r(((buyback or 0) + (divs or 0)) / market_cap * 100
                                        if market_cap and (buyback is not None or divs is not None) else None),
        "diluted_share_change_yoy_pct": _pct(
            _f(q, "weighted_average_diluted_shares"),
            _f(q_yoy, "weighted_average_diluted_shares")),
        "capex_to_revenue_pct": _r((_div(_f(ttm, "capital_expenditures"), _f(ttm, "revenue")) or 0) * 100
                                   if _div(_f(ttm, "capital_expenditures"), _f(ttm, "revenue")) is not None else None),
        "acquisition_spend_to_fcf_pct": _r((_div(_f(ttm, "acquisitions"), _f(ttm, "free_cash_flow")) or 0) * 100
                                           if _div(_f(ttm, "acquisitions"), _f(ttm, "free_cash_flow")) is not None else None),
    }


def bank_metrics(ttm: dict | None, quarters: list[dict]) -> dict:
    """Bank/consumer-finance adapter v1. NIM proxy uses latest total assets
    (not average earning assets — documented approximation)."""
    q = quarters[-1] if quarters else None
    q_yoy = quarters[-5] if len(quarters) >= 5 else None
    rev = _f(ttm, "revenue")
    nii = _f(ttm, "net_interest_income")
    assets = _f(ttm, "total_assets")
    equity = _f(ttm, "shareholders_equity")
    return {
        "total_net_revenue": rev,
        "net_interest_income": nii,
        "noninterest_income": _f(ttm, "noninterest_income"),
        "efficiency_ratio_pct": _r((_div(_f(ttm, "noninterest_expense"), rev)
                                    or 0) * 100
                                   if _div(_f(ttm, "noninterest_expense"), rev)
                                   is not None else None),
        "nim_proxy_pct": _r((_div(nii, assets) or 0) * 100
                            if _div(nii, assets) is not None else None),
        "provisions_to_revenue_pct": _r(
            (_div(_f(ttm, "provision_for_credit_losses"), rev) or 0) * 100
            if _div(_f(ttm, "provision_for_credit_losses"), rev) is not None
            else None),
        "equity_to_assets_pct": _r((_div(equity, assets) or 0) * 100
                                   if _div(equity, assets) is not None else None),
        "total_deposits": _f(q, "total_deposits"),
        "deposits_yoy_pct": _pct(_f(q, "total_deposits"),
                                 _f(q_yoy, "total_deposits")),
        "note": "bank adapter v1: NIM proxy = TTM NII / latest total assets "
                "(not avg earning assets); loan-flow-driven cash-flow "
                "metrics are suppressed as non-meaningful",
    }


def valuation_metrics(ttm: dict | None, price: float | None,
                      market_cap: float | None,
                      enterprise_value: float | None,
                      ttm_history: list[tuple[str, float | None]] | None = None,
                      price_lookup=None) -> dict:
    """ttm_history: [(available_at, ttm_diluted_eps)] on one split basis.
    price_lookup must use the same split basis, without dividend adjustments."""
    eps = _f(ttm, "diluted_eps")
    ni = _f(ttm, "net_income")
    fcf = _f(ttm, "free_cash_flow")
    rev = _f(ttm, "revenue")
    oi = _f(ttm, "operating_income")
    pe = _div(price, eps) if eps and eps > 0 else None
    out = {
        "pe_ttm": _r(pe),
        "price_to_fcf_ttm": _r(_div(market_cap, fcf) if fcf and fcf > 0 else None),
        "ev_to_revenue_ttm": _r(_div(enterprise_value, rev)),
        "ev_to_operating_income_ttm": _r(_div(enterprise_value, oi) if oi and oi > 0 else None),
        "fcf_yield_pct": _r((_div(fcf, market_cap) or 0) * 100
                            if _div(fcf, market_cap) is not None else None),
        "earnings_yield_pct": _r((_div(ni, market_cap) or 0) * 100
                                 if _div(ni, market_cap) is not None else None),
        "pe_5y_percentile": None,
    }
    if pe and ttm_history and price_lookup:
        history_pes = []
        for period_end, hist_eps in ttm_history[-20:]:  # ~5y of quarters
            if not hist_eps or hist_eps <= 0:
                continue
            px = price_lookup(period_end)
            if px:
                history_pes.append(px / hist_eps)
        if len(history_pes) >= 8:
            below = sum(1 for h in history_pes if h <= pe)
            out["pe_5y_percentile"] = _r(below / len(history_pes) * 100, 1)
    return out
