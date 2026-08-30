"""Analyst specification for HIMS, based on the 2026-Q2 filing/release.

Scenario fair values are precomputed from an EV/revenue framework because
current GAAP EPS/FCF is not a stable valuation denominator. The report keeps
these assumptions explicit so an LLM never invents the arithmetic.
"""
from __future__ import annotations

HIMS_SPEC = {
    "business_assessment": {
        "summary": (
            "Consumer-first telehealth and personalized-care platform spanning "
            "weight loss, sexual health, dermatology, hair loss, hormone and "
            "mental-health categories. Q2 2026 headline growth is strong, but "
            "its quality is mixed: U.S. revenue grew much more slowly than "
            "consolidated revenue and recent acquisitions materially enlarged "
            "the international contribution."
        ),
    },
    "fundamental_trend": {
        "direction": "MIXED",
        "strength": "STRONG",
        "primary_drivers": [
            "Q2 2026 revenue $753.2M, +38% YoY; subscribers 2.891M, +19% YoY",
            "Monthly revenue per average subscriber rose 21% YoY to $92",
            "Q3 revenue guide $880M-$900M and FY2026 revenue guide raised to $3.1B-$3.3B",
        ],
        "primary_deteriorations": [
            "Gross margin fell to 64% from 76% a year earlier",
            "Adjusted EBITDA fell to $60.3M from $82.2M despite 38% revenue growth",
            "Q2 free cash flow was -$68.2M and GAAP net loss was $86.3M",
            "U.S. revenue grew only 16%; much of Rest-of-World growth reflects acquisitions including Eucalyptus",
        ],
    },
    "forecast_drivers": [
        "Whether U.S. organic growth reaccelerates after the GLP-1/product-mix transition",
        "Whether gross margin and adjusted EBITDA margin recover as branded GLP-1 and international mix scale",
        "Ability to convert subscriber/ARPU growth into durable free cash flow",
        "Regulatory/legal outcomes and availability/economics of weight-loss treatments",
        "Integration quality and capital intensity of international acquisitions",
    ],
    "scenarios": [
        {
            "name": "bear",
            "probability": 0.30,
            "fair_value": 19.8,
            "valuation_method": "2027 revenue x EV/sales less net debt",
            "revenue_2027_b": 3.80,
            "ev_sales_multiple": 1.35,
            "net_debt_b": 0.524,
            "diluted_shares_m": 233.0,
        },
        {
            "name": "base",
            "probability": 0.50,
            "fair_value": 36.1,
            "valuation_method": "2027 revenue x EV/sales less net debt",
            "revenue_2027_b": 4.25,
            "ev_sales_multiple": 2.10,
            "net_debt_b": 0.524,
            "diluted_shares_m": 233.0,
        },
        {
            "name": "bull",
            "probability": 0.20,
            "fair_value": 55.6,
            "valuation_method": "2027 revenue x EV/sales less net debt",
            "revenue_2027_b": 4.65,
            "ev_sales_multiple": 2.90,
            "net_debt_b": 0.524,
            "diluted_shares_m": 233.0,
        },
    ],
    "investment_thesis": {
        "summary": (
            "At roughly $29, HIMS has a potentially favorable long-term "
            "revaluation setup if management can preserve 20%+ growth while "
            "rebuilding margins and cash conversion. The stock is not a clean "
            "high-quality compounder today: Q2 showed significant margin "
            "compression, negative free cash flow, acquisition-driven growth "
            "and rising capital-structure complexity. The opportunity is a "
            "margin-recovery/expectations-gap thesis, not simply a revenue-growth thesis."
        ),
        "why_market_may_be_wrong": [
            "Q3 revenue guidance is materially above the run-rate implied by Q2 and FY2026 revenue guidance was raised",
            "Subscriber growth plus higher revenue per subscriber can create substantial operating leverage if product costs normalize",
            "At around 2x FY2026 sales, the equity does not require a software-like revenue multiple to generate upside if margins recover",
        ],
        "what_is_already_priced_in": [
            "A meaningful recovery in profitability: current cash earnings do not support the equity valuation on their own",
            "Continued strong weight-loss demand and successful international integration",
        ],
        "catalysts": [
            "Q3/Q4 evidence that gross margin stabilizes or recovers while revenue remains above 20% growth",
            "Improving adjusted EBITDA margin and return to sustained positive free cash flow",
            "Better clarity around branded GLP-1 economics, regulation and supplier relationships",
        ],
        "risks": [
            "Gross-margin compression can overwhelm revenue growth if branded pharmaceutical/product costs remain structurally higher",
            "Acquisition-driven international growth can mask weaker organic U.S. momentum",
            "Convertible debt, legal contingencies and regulatory actions increase downside convexity",
            "A broad bear market could compress the multiple before margin recovery becomes visible",
        ],
        "invalidation_conditions": [
            "Two consecutive quarters with consolidated revenue growth below 20% without offsetting margin expansion",
            "Adjusted EBITDA margin remaining below 10% through the first half of 2027",
            "Free cash flow remaining materially negative after working-capital effects normalize",
            "Material adverse regulatory action that impairs a major weight-loss offering or customer-acquisition channel",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": (
            "The 38% headline growth rate overstates underlying momentum: U.S. "
            "revenue grew 16%, international growth was acquisition-heavy, gross "
            "margin fell 12 points, EBITDA declined and free cash flow remained "
            "negative. If those economics are structural rather than transitional, "
            "HIMS is a lower-margin healthcare distributor/platform being valued "
            "for future operating leverage that may not arrive."
        ),
        "fragile_assumptions": [
            "Base case assumes 2027 revenue around $4.25B and a recovery toward a 2.1x EV/sales multiple",
            "Net-debt treatment assumes convertible principal is economically debt-like while available liquidity remains usable",
            "International acquisitions are assumed to add durable revenue without proportionate ongoing integration costs",
        ],
        "accounting_concerns": [
            "Six-month operating cash flow includes very large receivable/payable working-capital movements, reducing confidence in headline cash conversion",
            "Stock-based compensation remains a meaningful non-cash reconciliation item",
        ],
        "valuation_concerns": [
            "Current profitability is weak: FY2026 adjusted EBITDA guidance midpoint is only about $300M on roughly $3.2B revenue",
            "Current/near-term free-cash-flow yield is low, so valuation support depends on future margin expansion",
        ],
        "unresolved_questions": [
            "Organic revenue growth excluding acquired international businesses",
            "Steady-state gross margin for branded GLP-1 and international product mix",
            "Normalized working-capital requirements at the new scale",
        ],
    },
    "conclusion": {
        "classification": "WATCH",
        "conviction": "MEDIUM",
        "risk_reward_score": 60,
    },
    "claims": [
        {
            "claim": "Q2 2026 revenue was $753.2M, up 38% YoY, while subscribers grew 19% to 2.891M.",
            "classification": "FACT",
            "source_ids": ["SEC:ACCESSION:0001773751-26-000163"],
        },
        {
            "claim": "Q2 gross margin was 64% versus 76% a year earlier, adjusted EBITDA was $60.3M versus $82.2M, and free cash flow was -$68.2M.",
            "classification": "FACT",
            "source_ids": ["SEC:ACCESSION:0001773751-26-000163", "SEC:ACCESSION:0001773751-26-000161"],
        },
        {
            "claim": "U.S. Q2 revenue grew 16%, while Rest-of-World revenue growth was primarily driven by recent acquisitions including Eucalyptus.",
            "classification": "FACT",
            "source_ids": ["SEC:ACCESSION:0001773751-26-000163"],
        },
        {
            "claim": "The principal upside thesis requires margin and free-cash-flow recovery rather than revenue growth alone.",
            "classification": "INFERENCE",
            "source_ids": ["SEC:ACCESSION:0001773751-26-000163"],
        },
        {
            "claim": "The 30/50/20 bear/base/bull scenario set implies a probability-weighted value above the late-August 2026 market price, but scenario probabilities are uncalibrated.",
            "classification": "FORECAST",
            "source_ids": [],
        },
    ],
}
