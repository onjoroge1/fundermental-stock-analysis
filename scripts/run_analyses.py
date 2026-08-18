"""Analyst-layer reports for the initial coverage names.

The narrative and scenario assumptions here are the analyst layer's judgment,
written against bundle evidence (every claim cites accession numbers or the
bundle's derived-metric engine). All arithmetic goes through valuation_tools —
the same calculators the MCP server exposes. Probabilities are judgment, NOT
calibrated: without point-in-time consensus the sufficiency gate prohibits
calibrated forecasts, so every forecast carries confidence LOW and the report
says so.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.bundle import build_bundle
from stock_machine.config import REPORT_DIR, ensure_dirs
from stock_machine.report_schema import validate_analysis_report
from stock_machine.valuation_tools import (calculate_expected_return,
                                           calculate_scenario_values)

DISCLAIMER = ("Machine-generated research output for tooling development; "
              "not investment advice. Scenario probabilities are analyst-layer "
              "judgment and are uncalibrated (no point-in-time consensus or "
              "backtest yet).")


FORECAST_METHOD_NOTE = (
    "3- and 6-month values assume linear convergence from the current price "
    "toward each scenario's 12-month fair value — a stated modeling "
    "assumption, not a calibrated short-horizon signal. P(positive) is "
    "therefore identical across horizons by construction. Confidence is LOW "
    "at every horizon: no point-in-time consensus exists to calibrate against.")


def multi_horizon_forecasts(scenarios: list[dict], price: float,
                            drivers: list[str]) -> dict:
    """3/6/12-month projections derived from the 12-month scenario set."""
    out = {}
    for label, months in (("three_month", 3), ("six_month", 6),
                          ("twelve_month", 12)):
        frac = months / 12.0
        outcomes = [
            {"probability": s["probability"],
             "price": round(price + (s["fair_value"] - price) * frac, 2)}
            for s in scenarios
        ]
        er = calculate_expected_return(price, outcomes)
        base = next(o["price"] for o, s in zip(outcomes, scenarios)
                    if s["name"] == "base")
        out[label] = {
            "expected_return_pct": er["expected_return_pct"],
            "expected_price": er["expected_price"],
            "fair_value_low": er["downside_price"],
            "fair_value_base": base,
            "fair_value_high": er["upside_price"],
            "probability_of_positive_return":
                er["probability_of_positive_return"],
            "confidence": "LOW",
            "drivers": drivers,
        }
    out["method_note"] = FORECAST_METHOD_NOTE
    return out


def build_report(ticker: str, spec: dict) -> dict:
    b = build_bundle(ticker)
    ms = b["market_snapshot"]
    price = ms["price"]
    scen = calculate_scenario_values(spec["scenarios"])
    outcomes = [{"probability": s["probability"], "price": s["fair_value"]}
                for s in scen["scenarios"]]
    er = calculate_expected_return(price, outcomes)

    report = {
        "analysis_schema_version": "1.0.0",
        "analysis_id": f"{ticker}__{b['knowledge_cutoff'][:10]}__RUN001",
        "ticker": ticker,
        "as_of": b["knowledge_cutoff"],
        "disclaimer": DISCLAIMER,
        "data_sufficiency": {
            "status": b["data_sufficiency"]["status"],
            "missing_information": b["data_quality"]["missing_datasets"],
            "limitations": b["data_quality"]["known_limitations"]
            + ["Expectations analysis omitted: no point-in-time consensus. "
               "Scenario probabilities are uncalibrated judgment."],
        },
        # judgment-only text; numeric pseudo-scores removed — anything that
        # looks computed must actually be computed (no synthetic values)
        "business_assessment": {"summary": spec["business_assessment"]["summary"]},
        "fundamental_scores": b["fundamental_scores"]["components"]
        | {"composite": b["fundamental_scores"]["composite_score"]},
        "fundamental_trend": spec["fundamental_trend"],
        "forecasts": multi_horizon_forecasts(
            scen["scenarios"], price, spec["forecast_drivers"]),
        "scenarios": scen["scenarios"],
        "investment_thesis": spec["investment_thesis"],
        "adversarial_review": spec["adversarial_review"],
        "conclusion": spec["conclusion"] | {
            "allowed_values": ["ATTRACTIVE", "WATCH", "UNATTRACTIVE",
                               "INSUFFICIENT_DATA"],
            "time_horizon": "12_MONTHS",
        },
        "claims": spec["claims"],
    }
    return report


def save(report: dict) -> None:
    validate_analysis_report(report)
    ticker = report["ticker"]
    ensure_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_id = f"{ticker}__{report['as_of'][:10]}__{stamp}"
    out_dir = REPORT_DIR / ticker
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{report_id}.json").write_text(json.dumps(report, indent=1))
    conn = db.connect()
    try:
        db.save_report(conn, report_id, ticker, report["as_of"], report)
    finally:
        conn.close()
    print(f"saved {report_id}  E[r]={report['forecasts']['twelve_month']['expected_return_pct']}%  "
          f"{report['conclusion']['classification']}")


SPECS: dict[str, dict] = {}

SPECS["AAPL"] = {
    "business_assessment": {
        "summary": "Consumer hardware franchise with an attached high-margin "
                   "services layer; revenue is transactional at the device "
                   "level but retention is ecosystem-driven. FY2026 shows a "
                   "sharp reacceleration after three roughly flat years.",
        "business_quality_score": 85, "competitive_position_score": 90,
        "cyclicality_score": 55,
    },
    "fundamental_trend": {
        "direction": "IMPROVING", "strength": "STRONG",
        "primary_drivers": [
            "Revenue +16.6% YoY (TTM basis) after a 1.8% three-year CAGR — a clear break in trend",
            "Incremental operating margin of 39.2% vs 32.6% base operating margin",
            "FCF +28.0% YoY with FCF/NI conversion of 1.05",
        ],
        "primary_deteriorations": [
            "Buyback yield compressed to 1.6% of market cap as the market cap re-rated",
        ],
    },
    "forecast_drivers": [
        "Whether the FY2026 revenue surge is a durable upgrade supercycle or a pull-forward",
        "Multiple sustainability: P/E 40.0 sits at the 95th percentile of own 5-year history",
    ],
    "scenarios": [
        {"name": "bear", "probability": 0.30, "eps": 8.60, "valuation_multiple": 28.0},
        {"name": "base", "probability": 0.50, "eps": 9.40, "valuation_multiple": 32.0},
        {"name": "bull", "probability": 0.20, "eps": 10.20, "valuation_multiple": 38.0},
    ],
    "investment_thesis": {
        "summary": "The fundamentals are excellent and improving — the problem "
                   "is the starting price. At 40× TTM earnings (95th percentile "
                   "of its own five-year range) the market already capitalizes "
                   "a durable growth reacceleration. If growth normalizes back "
                   "toward the pre-FY2026 trend, multiple compression dominates "
                   "EPS growth over a 12-month horizon.",
        "why_market_may_be_wrong": [
            "If the reacceleration is a multi-year replacement cycle rather than a one-off, today's multiple can hold",
        ],
        "what_is_already_priced_in": [
            "Continuation of double-digit revenue growth (P/E at own 95th percentile)",
            "+56.4% trailing-12-month share-price move vs +21.8% EPS growth — most of the return came from re-rating",
        ],
        "catalysts": [
            "Next two quarterly filings: does YoY growth hold above ~10%?",
            "Capital-return step-up at the fall guidance reset",
        ],
        "risks": [
            "Multiple reversion: at the five-year median multiple the same EPS implies a materially lower price",
            "Hardware cycle dependency: 3-year revenue CAGR before FY2026 was 1.8%",
        ],
        "invalidation_conditions": [
            "Two consecutive quarters of YoY revenue growth below 5% would invalidate the reacceleration premise",
            "FCF/NI conversion falling below 0.9 would invalidate the earnings-quality premise",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": "A 95th-percentile multiple on cycle-peak "
            "hardware earnings: FY2026-Q1 revenue of $143.8B against $124.3B a "
            "year earlier is the kind of comp that gets lapped hard, and at 40× "
            "earnings the stock has no valuation cushion when growth mean-reverts "
            "toward its 1.8% three-year CAGR.",
        "fragile_assumptions": [
            "Base case assumes 13% forward EPS growth — roughly double the pre-FY2026 trend",
            "Bear-case multiple of 28× is still above the 5-year median; a return to trough multiples (~22×) is not modeled",
        ],
        "accounting_concerns": [],
        "valuation_concerns": [
            "P/E 40.0 at 95th percentile of own history; EV/operating income 33.3",
            "FCF yield of 2.6% offers little support if growth disappoints",
        ],
        "unresolved_questions": [
            "Product-level driver of the FY2026 surge cannot be isolated without segment data in the bundle",
        ],
    },
    "conclusion": {"classification": "WATCH", "conviction": "MEDIUM",
                   "risk_reward_score": 45},
    "claims": [
        {"claim": "TTM revenue is $451.4B with revenue up 16.6% YoY, against a 1.8% three-year CAGR.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0000320193-26-000013", "SEC:ACCESSION:0000320193-26-000006"]},
        {"claim": "TTM diluted EPS is $8.32 and the shares trade at 40.0× that figure, the 95th percentile of the trailing five years.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0000320193-26-000013", "YAHOO:CHART:AAPL"]},
        {"claim": "The growth reacceleration appears cyclical rather than structural, given the flat 2022-2025 base period.",
         "classification": "INFERENCE",
         "source_ids": ["SEC:ACCESSION:0000320193-25-000079"]},
        {"claim": "Probability-weighted 12-month value is below the current price under 30/50/20 bear/base/bull weights.",
         "classification": "FORECAST", "source_ids": []},
    ],
}

SPECS["MSFT"] = {
    "business_assessment": {
        "summary": "Enterprise software and cloud franchise with contractual, "
                   "recurring revenue. Currently in a heavy AI capital-"
                   "investment phase: capex is 30.6% of revenue, which is "
                   "depressing free cash flow while income-statement growth "
                   "stays strong.",
        "business_quality_score": 92, "competitive_position_score": 90,
        "cyclicality_score": 30,
    },
    "fundamental_trend": {
        "direction": "MIXED", "strength": "MODERATE",
        "primary_drivers": [
            "Revenue +18.3% YoY on a $318B base; 12.4% three-year CAGR",
            "Operating margin 46.8% with 55.6% incremental margin",
            "Net cash balance sheet (net debt −$38.0B), interest coverage 191×",
        ],
        "primary_deteriorations": [
            "FCF −22.2% YoY: capex at 30.6% of revenue cut FCF/NI conversion to 0.58",
            "Share price −24.7% over 12 months while EPS grew +23.4%",
        ],
    },
    "forecast_drivers": [
        "Whether AI infrastructure spend converts to revenue at acceptable returns",
        "De-rated multiple: P/E 22.7 sits at the 5th percentile of own 5-year history",
    ],
    "scenarios": [
        {"name": "bear", "probability": 0.25, "eps": 17.50, "valuation_multiple": 19.0},
        {"name": "base", "probability": 0.50, "eps": 19.30, "valuation_multiple": 24.0},
        {"name": "bull", "probability": 0.25, "eps": 20.50, "valuation_multiple": 28.0},
    ],
    "investment_thesis": {
        "summary": "An 18%-growth, 47%-operating-margin business now trades at "
                   "the cheapest earnings multiple of its own past five years "
                   "because the market is treating the AI capex cycle as value "
                   "destruction. If even the income statement alone is taken at "
                   "face value, the de-rating overshoots; the FCF bear case "
                   "requires capex to stay above 30% of revenue indefinitely.",
        "why_market_may_be_wrong": [
            "Capex is discretionary in a way opex is not: the FCF trough is partly a timing artifact of front-loaded datacenter build",
            "A 5th-percentile multiple prices a growth slowdown the income statement does not yet show",
        ],
        "what_is_already_priced_in": [
            "Sustained FCF impairment: P/FCF of 38.9 vs P/E of 22.7 shows the market is valuing the depressed cash flow, not earnings",
        ],
        "catalysts": [
            "First quarter of capex-to-revenue declining sequentially",
            "Cloud growth holding while depreciation from the build-out flows through",
        ],
        "risks": [
            "If AI capacity is overbuilt, depreciation compresses future operating margins",
            "Inventory (datacenter components) grew 25pt faster than revenue YoY",
        ],
        "invalidation_conditions": [
            "Revenue growth below 10% YoY for two consecutive quarters",
            "Capex-to-revenue still above 30% four quarters from now with cloud growth decelerating",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": "The market is not mispricing the earnings — "
            "it is repricing their capital intensity. If ~$97B of annualized "
            "capex is what maintaining competitive position now costs, "
            "owner earnings are closer to the $72.9B FCF than the $125.2B net "
            "income, and on that basis the stock trades at ~39× — not cheap.",
        "fragile_assumptions": [
            "Base case treats depreciation step-up as manageable inside a 46.8% operating margin; a large overbuild breaks this",
            "Bear multiple of 19× assumes the market never prices MSFT below its 2018 levels — a regime assumption",
        ],
        "accounting_concerns": [
            "FCF/NI of 0.58 means reported earnings currently overstate distributable cash by ~40%",
        ],
        "valuation_concerns": [
            "P/FCF 38.9 is expensive if the capex level proves structural",
        ],
        "unresolved_questions": [
            "Cloud segment growth cannot be isolated without segment data in the bundle",
        ],
    },
    "conclusion": {"classification": "ATTRACTIVE", "conviction": "MEDIUM",
                   "risk_reward_score": 68},
    "claims": [
        {"claim": "TTM revenue is $318.3B, +18.3% YoY, with a 46.8% operating margin.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001193125-26-191507"]},
        {"claim": "Capex is 30.6% of revenue and TTM FCF fell 22.2% YoY to $72.9B (FCF/NI 0.58).",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001193125-26-191507", "SEC:ACCESSION:0001193125-26-027207"]},
        {"claim": "P/E of 22.7 is at the 5th percentile of the company's own five-year range while the share price fell 24.7% over twelve months.",
         "classification": "FACT", "source_ids": ["YAHOO:CHART:MSFT"]},
        {"claim": "The FCF depression is substantially a front-loaded investment cycle rather than a permanent step-down.",
         "classification": "INFERENCE",
         "source_ids": ["SEC:ACCESSION:0001193125-26-191507"]},
        {"claim": "Probability-weighted 12-month value is above the current price under 25/50/25 weights.",
         "classification": "FORECAST", "source_ids": []},
    ],
}

SPECS["NVDA"] = {
    "business_assessment": {
        "summary": "Dominant AI accelerator supplier. Extraordinary growth "
                   "(+85% YoY on a $253B TTM base) and margins (64% operating), "
                   "but demand is concentrated in a small number of hyperscaler "
                   "buyers whose own capex cycles are the real demand signal.",
        "business_quality_score": 88, "competitive_position_score": 92,
        "cyclicality_score": 80,
    },
    "fundamental_trend": {
        "direction": "IMPROVING", "strength": "STRONG",
        "primary_drivers": [
            "Revenue +85.2% YoY; five straight quarters of sequential growth (44.1B → 81.6B)",
            "Operating margin expanded from 49.1% to 65.6% over five quarters",
        ],
        "primary_deteriorations": [
            "Earnings-quality score 51.7: accrual ratio +13.1% of assets, OCF/NI 0.79",
            "Inventory grew 42.4pt faster than revenue YoY",
        ],
    },
    "forecast_drivers": [
        "Hyperscaler capex budgets (MSFT alone is at 30%+ capex-to-revenue — sustainability of that spend IS NVDA's revenue line)",
        "Inventory build: supply catching up to demand compresses pricing power",
    ],
    "scenarios": [
        {"name": "bear", "probability": 0.30, "eps": 6.00, "valuation_multiple": 20.0},
        {"name": "base", "probability": 0.45, "eps": 8.20, "valuation_multiple": 26.0},
        {"name": "bull", "probability": 0.25, "eps": 9.80, "valuation_multiple": 32.0},
    ],
    "investment_thesis": {
        "summary": "The strongest operating momentum in the coverage set, at a "
                   "multiple (31.6× TTM) that is not extreme — but the cash "
                   "flow statement is starting to disagree with the income "
                   "statement. Accruals at 13.1% of assets and a 42-point "
                   "inventory-versus-revenue growth gap are the classic "
                   "signature of a cycle approaching its supply-demand "
                   "crossover.",
        "why_market_may_be_wrong": [
            "If AI inference demand compounds independently of training build-out, the cycle framing is wrong and the base/bull cases are conservative",
        ],
        "what_is_already_priced_in": [
            "Continued hypergrowth: EV/revenue of 19.7 requires years of elevated growth to underwrite",
        ],
        "catalysts": [
            "Next quarter's inventory line versus revenue growth",
            "Hyperscaler capex guidance revisions in either direction",
        ],
        "risks": [
            "Customer concentration: a single large buyer pausing orders moves the whole revenue line",
            "OCF/NI below 0.8 while receivables and inventory build — earnings are running ahead of cash",
        ],
        "invalidation_conditions": [
            "Inventory growth exceeding revenue growth for two more consecutive quarters",
            "First sequential revenue decline would invalidate the momentum premise entirely",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": "Every semiconductor supercycle in history "
            "ended with exactly this pattern: record margins, inventory "
            "building faster than sales, and earnings outrunning operating "
            "cash flow. If hyperscaler capex plateaus, revenue can fall while "
            "inventory writes down — a double hit to EPS at a still-premium "
            "multiple.",
        "fragile_assumptions": [
            "Base case assumes 25% forward EPS growth with no margin give-back from the current 64% — historically peak — operating margin",
            "All scenarios assume the multiple floors at 20×; prior semi downcycles have printed lower",
        ],
        "accounting_concerns": [
            "Accrual ratio of +13.1% of assets is the worst in the coverage set",
            "Inventory-revenue growth gap of +42.4pt",
        ],
        "valuation_concerns": [
            "EV/revenue 19.7 and P/FCF 42.0 leave no room for a growth air-pocket",
        ],
        "unresolved_questions": [
            "Customer concentration percentages are not in the bundle (10-K concentration disclosures not yet parsed)",
        ],
    },
    "conclusion": {"classification": "WATCH", "conviction": "MEDIUM",
                   "risk_reward_score": 50},
    "claims": [
        {"claim": "TTM revenue is $253.5B (+85.2% YoY) with a 64.0% operating margin; the latest quarter printed $81.6B at 65.6%.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001045810-26-000052"]},
        {"claim": "Accruals are +13.1% of assets, OCF/NI is 0.79, and inventory grew 42.4 points faster than revenue YoY.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001045810-26-000052", "SEC:ACCESSION:0001045810-26-000021"]},
        {"claim": "The widening gap between reported earnings and operating cash flow indicates the cycle is late-stage.",
         "classification": "INFERENCE",
         "source_ids": ["SEC:ACCESSION:0001045810-26-000052"]},
        {"claim": "Probability-weighted 12-month value is approximately the current price under 30/45/25 weights — the distribution is wide, not favorable.",
         "classification": "FORECAST", "source_ids": []},
    ],
}

SPECS["META"] = {
    "business_assessment": {
        "summary": "Advertising platform with 81.9% gross margins and "
                   "re-accelerated growth (+33.1% YoY), running a large AI "
                   "capex program (35.2% of revenue). Highest composite "
                   "fundamental score in the coverage set (81.5).",
        "business_quality_score": 88, "competitive_position_score": 85,
        "cyclicality_score": 60,
    },
    "fundamental_trend": {
        "direction": "IMPROVING", "strength": "STRONG",
        "primary_drivers": [
            "Revenue +33.1% YoY, EPS +62.4% YoY",
            "Operating margin steady around 41% while absorbing the capex program's depreciation",
            "Receivables growing 12.7pt SLOWER than revenue — high-quality growth",
        ],
        "primary_deteriorations": [
            "FCF/NI conversion 0.68 on capex at 35.2% of revenue",
            "Stock comp is 10.4% of revenue — the highest in the coverage set",
        ],
    },
    "forecast_drivers": [
        "Ad-market cycle vs AI-driven engagement and pricing gains",
        "Capex normalization timeline; whether the 12-month −16.5% de-rating reverses",
    ],
    "scenarios": [
        {"name": "bear", "probability": 0.25, "eps": 28.00, "valuation_multiple": 17.0},
        {"name": "base", "probability": 0.50, "eps": 33.00, "valuation_multiple": 22.0},
        {"name": "bull", "probability": 0.25, "eps": 36.50, "valuation_multiple": 26.0},
    ],
    "investment_thesis": {
        "summary": "The machine's highest-scored fundamentals at the second-"
                   "cheapest multiple in the set: 33% growth, 41% operating "
                   "margins, net cash, at 21.6× TTM earnings (25th percentile "
                   "of own history) after a 16.5% twelve-month drawdown. The "
                   "bear case runs through the ad cycle and SBC dilution, not "
                   "through the operating business.",
        "why_market_may_be_wrong": [
            "The de-rating treats AI capex as a Reality-Labs-style money pit; the receivables and margin data show the core business absorbing it without strain",
        ],
        "what_is_already_priced_in": [
            "An ad-spend slowdown: a 21.6× multiple on 33% growth implies the market expects sharp deceleration",
        ],
        "catalysts": [
            "Next quarter's ad revenue growth print",
            "Any disclosed capex plateau",
        ],
        "risks": [
            "Advertising is macro-cyclical; a downturn hits revenue and the multiple simultaneously",
            "SBC at 10.4% of revenue quietly transfers ~$22B/year of value to employees",
        ],
        "invalidation_conditions": [
            "Revenue growth below 15% YoY for two consecutive quarters",
            "Operating margin below 35% while capex remains above 30% of revenue",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": "GAAP EPS flatters the economics: with SBC at "
            "10.4% of revenue and capex at 35.2%, true owner earnings are far "
            "below net income. On FCF the stock trades at 31.6× — mid-pack, "
            "not cheap — and the growth rate is hostage to the ad cycle.",
        "fragile_assumptions": [
            "Base case assumes 20% EPS growth continues while lapping a +62% EPS comp — deceleration risk is high",
            "Bear multiple of 17× has only held in severe ad recessions; 2022 printed lower",
        ],
        "accounting_concerns": [
            "SBC of ~10.4% of revenue makes per-share metrics materially better than whole-company economics",
        ],
        "valuation_concerns": [
            "P/FCF of 31.6 is the honest multiple during the capex program",
        ],
        "unresolved_questions": [
            "Family-of-apps vs Reality Labs split is not in the bundle (segment parsing is Phase 2)",
        ],
    },
    "conclusion": {"classification": "ATTRACTIVE", "conviction": "MEDIUM",
                   "risk_reward_score": 70},
    "claims": [
        {"claim": "TTM revenue is $215.0B (+33.1% YoY) with a 41.2% operating margin and $70.6B net income.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001628280-26-028526"]},
        {"claim": "P/E of 21.6 sits at the 25th percentile of the company's own five-year range after a −16.5% twelve-month share-price move.",
         "classification": "FACT", "source_ids": ["YAHOO:CHART:META"]},
        {"claim": "Receivables growing 12.7 points slower than revenue indicates the reported growth is cash-collected, not channel-stuffed.",
         "classification": "INFERENCE",
         "source_ids": ["SEC:ACCESSION:0001628280-26-028526"]},
        {"claim": "Probability-weighted 12-month value is above the current price under 25/50/25 weights.",
         "classification": "FORECAST", "source_ids": []},
    ],
}


SPECS["GOOGL"] = {
    "business_assessment": {
        "summary": "Search/ads franchise plus cloud, in a heavy AI capex phase "
                   "(29.7% of revenue). CRITICAL READING NOTE: GAAP net income "
                   "($244.2B TTM) is inflated ~65% above operating income "
                   "($147.6B) by non-operating investment gains, so the "
                   "headline P/E of 16.1 and 54.8% net margin overstate the "
                   "operating business. This analysis anchors on operating "
                   "earnings throughout.",
    },
    "fundamental_trend": {
        "direction": "IMPROVING", "strength": "MODERATE",
        "primary_drivers": [
            "Revenue +24.2% YoY with operating income +30.4% — operating leverage is real",
            "Operating margin reached 34.1% in the latest quarter (40.8B on 119.8B)",
            "Net cash of $142.3B",
        ],
        "primary_deteriorations": [
            "FCF collapsed to $53.3B TTM (P/FCF 73.9) on capex at 29.7% of revenue",
            "OCF/NI 0.76 and accruals +6.4% of assets — but this is mostly the non-operating gains sitting in NI, not cash",
        ],
    },
    "forecast_drivers": [
        "Operating earnings power (~$10.2/share after-tax proxy), not the gain-inflated GAAP EPS",
        "Whether the +66.9% twelve-month re-rating already prices the AI-search transition resolving favorably",
    ],
    # eps values are AFTER-TAX OPERATING earnings-per-share proxies
    # (operating income × (1−15%) / diluted shares), stated as judgment
    "scenarios": [
        {"name": "bear", "probability": 0.25, "eps": 10.50, "valuation_multiple": 22.0},
        {"name": "base", "probability": 0.50, "eps": 12.00, "valuation_multiple": 26.0},
        {"name": "bull", "probability": 0.25, "eps": 13.50, "valuation_multiple": 30.0},
    ],
    "investment_thesis": {
        "summary": "The operating business is excellent and accelerating, but "
                   "after a +66.9% twelve-month run the stock is roughly "
                   "fairly priced on operating earnings (~26× the operating-"
                   "EPS proxy). The optically cheap 16× GAAP P/E is an "
                   "artifact of investment gains that cannot be capitalized "
                   "as recurring earnings.",
        "why_market_may_be_wrong": [
            "If cloud + AI monetization keeps operating income compounding near 30%, today's operating multiple is not demanding",
        ],
        "what_is_already_priced_in": [
            "A successful AI-search transition: the twelve-month re-rating of +66.9% happened while the search-disruption narrative faded",
        ],
        "catalysts": [
            "Capex-to-revenue inflection; any quarter below ~27% would lift FCF sharply",
            "Continued 30%+ operating income growth",
        ],
        "risks": [
            "Non-operating gains reverse: the same mark-to-market that added ~$97B to earnings can subtract in a downdraft",
            "Regulatory remedies in search distribution",
        ],
        "invalidation_conditions": [
            "Operating income growth below 10% YoY for two consecutive quarters",
            "Capex-to-revenue above 30% for four more quarters without cloud acceleration",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": "Earnings quality is the worst-understood in "
            "the set: $96.6B of the TTM earnings are non-operating marks that "
            "produced no cash (FCF is $53.3B against $244.2B of net income — "
            "a 0.22 conversion). Value the cash machine honestly — ~$126B "
            "after-tax operating earnings, 74× free cash flow during the "
            "build-out — and the margin of safety at $319.74 is thin.",
        "fragile_assumptions": [
            "The 15% tax-rate proxy on operating income is a simplification; the true operating tax rate is not separable in the bundle",
            "Base case assumes the operating multiple holds at 26× while FCF stays depressed",
        ],
        "accounting_concerns": [
            "TTM net income exceeds operating income by >65% — flagged automatically by the data-quality gate",
            "SBC at 6.3% of revenue",
        ],
        "valuation_concerns": [
            "P/FCF 73.9; GAAP P/E percentile (0th) is meaningless because current-E is inflated",
        ],
        "unresolved_questions": [
            "Composition of the non-operating gains (which holdings, how liquid) requires 10-Q note parsing — Phase 2",
        ],
    },
    "conclusion": {"classification": "WATCH", "conviction": "MEDIUM",
                   "risk_reward_score": 52},
    "claims": [
        {"claim": "TTM revenue is $445.9B (+24.2% YoY); operating income is $147.6B (+30.4% YoY).",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001652044-26-000071", "SEC:ACCESSION:0001652044-26-000048"]},
        {"claim": "TTM net income of $244.2B exceeds operating income by $96.6B; OCF/NI is 0.76 and FCF/NI is 0.22.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001652044-26-000071"]},
        {"claim": "The GAAP P/E of 16.1 materially understates the operating valuation (~26× after-tax operating earnings proxy).",
         "classification": "INFERENCE",
         "source_ids": ["SEC:ACCESSION:0001652044-26-000071", "YAHOO:CHART:GOOGL"]},
        {"claim": "Probability-weighted 12-month value approximates the current price under 25/50/25 weights on operating-EPS scenarios.",
         "classification": "FORECAST", "source_ids": []},
    ],
}

SPECS["AMZN"] = {
    "business_assessment": {
        "summary": "Retail/logistics plus AWS. Operating income grew 29.6% on "
                   "16.6% revenue growth — the margin-expansion story is "
                   "intact — but capex at 20.3% of revenue pushed TTM free "
                   "cash flow negative (−$2.5B).",
    },
    "fundamental_trend": {
        "direction": "MIXED", "strength": "MODERATE",
        "primary_drivers": [
            "Revenue +16.6% YoY to a $742.8B TTM base",
            "Operating income +29.6% YoY; EPS +74.8%",
            "Incremental operating margin 14.9% vs 11.5% base — mix shifting toward AWS/ads",
        ],
        "primary_deteriorations": [
            "TTM FCF negative (−$2.5B) on capex at 20.3% of revenue",
            "Receivables grew 22.7pt faster than revenue — the largest such gap in the coverage set",
        ],
    },
    "forecast_drivers": [
        "AWS/advertising mix shift lifting consolidated margins",
        "Capex cycle timing; whether FCF turns positive within four quarters",
    ],
    "scenarios": [
        {"name": "bear", "probability": 0.25, "eps": 8.00, "valuation_multiple": 22.0},
        {"name": "base", "probability": 0.50, "eps": 10.00, "valuation_multiple": 26.0},
        {"name": "bull", "probability": 0.25, "eps": 11.50, "valuation_multiple": 30.0},
    ],
    "investment_thesis": {
        "summary": "Earnings are compounding well ahead of revenue as the mix "
                   "shifts to AWS and advertising, and at 27.8× TTM earnings "
                   "(26th percentile of own history) with a flat twelve-month "
                   "share price, the market is paying for none of the margin "
                   "trajectory. The offsets: zero current FCF support and a "
                   "receivables build that needs watching.",
        "why_market_may_be_wrong": [
            "A flat stock through a year of +75% EPS growth means the multiple compressed ~43% — the de-rating may have overshot the capex concern",
        ],
        "what_is_already_priced_in": [
            "Persistent FCF weakness; skepticism that the margin expansion survives the investment cycle",
        ],
        "catalysts": [
            "First positive TTM FCF print",
            "Continued >25% operating income growth against easing comps",
        ],
        "risks": [
            "Receivables growing 22.7pt faster than revenue (consumer credit exposure via BNPL/card programs) — a working-capital and credit-quality flag",
            "Retail consumer cyclicality on ~80% of revenue",
        ],
        "invalidation_conditions": [
            "Operating income growth below 10% for two consecutive quarters",
            "Receivables-revenue growth gap above 20pt again next quarter",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": "A $2.5T company with negative trailing free "
            "cash flow is priced entirely on forward margin promises. If AWS "
            "growth slows while the capex program runs, AMZN combines "
            "MSFT's cash-burn problem with a retail multiple's fragility — "
            "and the receivables build hints reported growth is partly "
            "vendor-financed.",
        "fragile_assumptions": [
            "Base case assumes ~20% EPS growth AND multiple stability — a double bet",
            "Scenario EPS treats the receivables build as benign; if it reverses into charge-offs, EPS growth stalls",
        ],
        "accounting_concerns": [
            "Receivables +22.7pt vs revenue growth is the set's largest divergence",
        ],
        "valuation_concerns": [
            "No FCF support at the current price; EV/operating income of 29.0 is above MSFT's 18.8 for a lower-margin business",
        ],
        "unresolved_questions": [
            "AWS vs retail split unavailable until segment parsing lands (Phase 2)",
        ],
    },
    "conclusion": {"classification": "WATCH", "conviction": "MEDIUM",
                   "risk_reward_score": 58},
    "claims": [
        {"claim": "TTM revenue is $742.8B (+16.6% YoY); operating income grew 29.6% and diluted EPS 74.8% YoY.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001018724-26-000014", "SEC:ACCESSION:0001018724-26-000004"]},
        {"claim": "TTM free cash flow is −$2.5B with capex at 20.3% of revenue.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001018724-26-000014"]},
        {"claim": "Receivables grew 22.7 points faster than revenue YoY, the widest gap in the coverage set.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001018724-26-000014"]},
        {"claim": "The margin-mix story is intact but currently unsupported by cash generation.",
         "classification": "INFERENCE",
         "source_ids": ["SEC:ACCESSION:0001018724-26-000014"]},
        {"claim": "Probability-weighted 12-month value is moderately above the current price under 25/50/25 weights.",
         "classification": "FORECAST", "source_ids": []},
    ],
}

SPECS["TSLA"] = {
    "business_assessment": {
        "summary": "Auto manufacturer with an energy business, priced as an "
                   "AI/robotics option. The fundamentals in the bundle "
                   "describe an 18.9%-gross-margin manufacturer with a 4.2% "
                   "operating margin and declining operating income; the "
                   "$1.24T market cap capitalizes businesses (robotaxi, "
                   "Optimus) that do not yet appear in the filings. This "
                   "machine scores only what is filed.",
    },
    "fundamental_trend": {
        "direction": "MIXED", "strength": "WEAK",
        "primary_drivers": [
            "Revenue +25.5% YoY (latest quarter $28.2B) — volume/energy recovery is real",
            "Fortress balance sheet: $34.5B net cash",
            "OCF/NI of 4.91 — cash generation far exceeds thin reported earnings",
        ],
        "primary_deteriorations": [
            "Operating income −56.9% YoY; incremental operating margin is NEGATIVE (−11.5%) — growth is coming at falling profitability",
            "TTM operating margin 4.2% vs ~17% at the 2022 peak",
        ],
    },
    "forecast_drivers": [
        "Whether pricing/mix stabilizes auto gross margins",
        "Any filed evidence of new-business revenue (robotaxi/energy storage scale-up)",
    ],
    "scenarios": [
        {"name": "bear", "probability": 0.35, "eps": 1.00, "valuation_multiple": 80.0},
        {"name": "base", "probability": 0.45, "eps": 1.60, "valuation_multiple": 120.0},
        {"name": "bull", "probability": 0.20, "eps": 2.50, "valuation_multiple": 200.0},
    ],
    "investment_thesis": {
        "summary": "On filed fundamentals the stock is the least attractive "
                   "in the coverage set: 291× TTM earnings for a business "
                   "whose operating income halved. Even the bull scenario "
                   "(EPS more than doubling AND a 200× multiple holding) "
                   "lands at $500 — the current price needs non-filed "
                   "optionality to be worth it. Verdict is UNATTRACTIVE on "
                   "fundamentals, with the explicit caveat that this machine "
                   "cannot price optionality it cannot see in filings.",
        "why_market_may_be_wrong": [
            "If robotaxi/Optimus revenue reaches filings at scale, the fundamental base changes discontinuously and this analysis resets",
        ],
        "what_is_already_priced_in": [
            "Successful commercialization of at least one large non-auto business: 291× earnings cannot be justified by the auto+energy P&L",
        ],
        "catalysts": [
            "First filed revenue from autonomous services",
            "Auto gross margin inflection after five quarters of pressure",
        ],
        "risks": [
            "Multiple compression toward even 100× on flat EPS is a −65% move",
            "Competition compressing auto pricing further",
        ],
        "invalidation_conditions": [
            "Filed operating margin recovering above 10% would invalidate the deterioration premise",
            "Disclosed autonomous-services revenue would invalidate the 'no filed optionality' premise",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": "This is the base case: a 4.2%-operating-"
            "margin manufacturer at 291× earnings with operating income down "
            "57% YoY. The scenario table's probability-weighted value sits "
            "far below the market price under any earnings-based framework.",
        "fragile_assumptions": [
            "The BULL case still assumes a 200× multiple — itself an extreme assumption kept only to bound the upside honestly",
            "Scenario probabilities on a story stock are the least reliable in the set; the distribution is effectively bimodal on non-filed outcomes",
        ],
        "accounting_concerns": [
            "None material — cash conversion (OCF/NI 4.91) is actually excellent; the issue is valuation, not accounting",
        ],
        "valuation_concerns": [
            "P/E 291 at the 80th percentile of own history; EV/operating income 274.9",
        ],
        "unresolved_questions": [
            "Energy vs auto segment economics need segment parsing (Phase 2)",
            "Regulatory-credit share of profitability is not separated in the bundle",
        ],
    },
    "conclusion": {"classification": "UNATTRACTIVE", "conviction": "LOW",
                   "risk_reward_score": 25},
    "claims": [
        {"claim": "TTM revenue is $103.6B (+25.5% YoY) but operating income fell 56.9% YoY to $4.4B (4.2% margin).",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001628280-26-049270", "SEC:ACCESSION:0001628280-26-026673"]},
        {"claim": "The shares trade at 291× TTM earnings and 274.9× EV/operating income, with $34.5B net cash.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001628280-26-049270", "YAHOO:CHART:TSLA"]},
        {"claim": "The market price capitalizes substantial revenue streams not present in any filing.",
         "classification": "INFERENCE",
         "source_ids": ["SEC:ACCESSION:0001628280-26-049270"]},
        {"claim": "Probability-weighted 12-month value is well below the current price even with a 200×-multiple bull case.",
         "classification": "FORECAST", "source_ids": []},
    ],
}


SPECS["UBER"] = {
    "business_assessment": {
        "summary": "Mobility + delivery marketplace with strong network "
                   "economics now converting to cash: TTM FCF $9.8B on "
                   "$53.7B revenue (18.3% FCF margin), operating income "
                   "+56.6% YoY. The stock fell 27.8% over twelve months and "
                   "the reverse DCF says the price now requires essentially "
                   "ZERO growth (+0.02%/yr) against a delivered 17.7% "
                   "3-year revenue CAGR — the widest negative expectations "
                   "gap in the coverage set. The market is pricing "
                   "autonomous-vehicle disruption as an existential threat.",
    },
    "fundamental_trend": {
        "direction": "IMPROVING", "strength": "STRONG",
        "primary_drivers": [
            "Operating income +56.6% YoY on +14.5% revenue — operating leverage compounding",
            "FCF conversion excellent: FCF/NI 1.15, accruals negative",
            "ROIC 21.5% with modest net debt ($4.4B)",
        ],
        "primary_deteriorations": [
            "GAAP EPS -84.3% YoY — prior-year equity-stake mark-ups, not operations (NI still exceeds OI by >25%, auto-flagged)",
        ],
    },
    "forecast_drivers": [
        "Whether AV commercialization is a threat to, or a volume source for, the marketplace layer",
        "FCF-per-share compounding (~$4.81 TTM) against a 13.7× P/FCF entry",
    ],
    # fair values = FCF/share × P/FCF multiple (judgment assumptions)
    "scenarios": [
        {"name": "bear", "probability": 0.25, "fair_value": 49.50},
        {"name": "base", "probability": 0.50, "fair_value": 81.00},
        {"name": "bull", "probability": 0.25, "fair_value": 119.70},
    ],
    "investment_thesis": {
        "summary": "A business compounding operating income at 56% trades at "
                   "13.7× free cash flow with a price that embeds zero "
                   "growth. Even the bear case (FCF/share fading to $4.50 "
                   "at 11×) is close to the current price; the base case "
                   "($5.40 at 15×) implies ~+23%. Historical setups with "
                   "this profile outperformed the universe 67% of the time "
                   "(median +22.7% excess, n=33).",
        "why_market_may_be_wrong": [
            "AV fear treats Uber as the disrupted party; the marketplace/demand layer can aggregate AV supply the way it aggregated drivers",
        ],
        "what_is_already_priced_in": [
            "Zero growth forever: the reverse DCF's +0.02%/yr requirement IS the bear thesis fully priced",
        ],
        "catalysts": [
            "Earnings 2026-08-05: bookings growth and AV-partnership economics",
            "Continued buybacks (capital-allocation score 93)",
        ],
        "risks": [
            "CEO discretionary sale of $479M in the trailing window — large even against a $20M director purchase; insider signal is net NEGATIVE",
            "AV players going direct-to-consumer would bypass the marketplace",
            "EPS optics remain noisy from equity-stake marks",
        ],
        "invalidation_conditions": [
            "Revenue growth below 8% for two consecutive quarters",
            "FCF margin below 12% (currently 18.3%)",
            "A major AV operator launching consumer-scale service outside the platform",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": "The zero-growth price is not a mistake — it "
            "is the market handicapping platform disintermediation by "
            "autonomy. If AV owners capture the demand relationship, "
            "take-rates compress industry-wide and today's 13.7× P/FCF is "
            "peak-cash-flow pricing. The $479M CEO sale is consistent with "
            "that reading.",
        "fragile_assumptions": [
            "Base case assumes 15× P/FCF holds through an AV narrative that could take years to resolve",
            "Surprise history is unreliable for this name (equity marks swing EPS ±350%) — the expectations score of 90 overstates operational beat quality",
        ],
        "accounting_concerns": [
            "TTM NI ($8.5B) > operating income ($6.3B) via non-operating items — use OI/FCF, not GAAP EPS",
        ],
        "valuation_concerns": [
            "P/E 16 is mark-inflated; EV/OI 22.2 is the cleaner multiple and is only moderately cheap",
        ],
        "unresolved_questions": [
            "Mobility vs delivery segment economics (segment parsing pending)",
        ],
    },
    "conclusion": {"classification": "ATTRACTIVE", "conviction": "MEDIUM",
                   "risk_reward_score": 68},
    "claims": [
        {"claim": "TTM revenue is $53.7B (+14.5% YoY); operating income grew 56.6% YoY; FCF is $9.8B (18.3% margin).",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001543151-26-000022", "SEC:ACCESSION:0001543151-26-000015"]},
        {"claim": "The current price implies ~0% 5-year cash-flow growth (reverse DCF, r=9%, tg=2.5%) vs a delivered 17.7% 3-year revenue CAGR.",
         "classification": "FACT",
         "source_ids": ["YAHOO:CHART:UBER", "SEC:ACCESSION:0001543151-26-000022"]},
        {"claim": "The CEO sold $479M of stock in a discretionary transaction within the trailing 6 months.",
         "classification": "FACT", "source_ids": ["SEC:FORM4:UBER"]},
        {"claim": "The market is pricing autonomous-vehicle disruption of the marketplace model.",
         "classification": "INFERENCE",
         "source_ids": ["YAHOO:CHART:UBER"]},
        {"claim": "Probability-weighted 12-month value is materially above the current price under 25/50/25 scenario weights.",
         "classification": "FORECAST", "source_ids": []},
    ],
}

SPECS["ADBE"] = {
    "business_assessment": {
        "summary": "Creative + document software monopoly with 89.4% gross "
                   "margins and a 40.8% FCF margin, de-rated 39.3% in a "
                   "year on generative-AI disruption fear. The reverse DCF "
                   "says today's price requires cash flow to DECLINE "
                   "10.4%/yr for five years — the market is pricing "
                   "secular decay, while delivered growth is +12.7% and "
                   "consensus still expects +5.2%.",
    },
    "fundamental_trend": {
        "direction": "STABLE", "strength": "MODERATE",
        "primary_drivers": [
            "Revenue +12.7% YoY, +10.5% 3-year CAGR — no decay in filed numbers yet",
            "ROIC 57.5%; FCF/NI 1.42; buybacks at a 100 capital-allocation score",
            "Beat streak intact: last four surprises +2.5%, +1.9%, +3.2%, +2.4%",
        ],
        "primary_deteriorations": [
            "Operating income growth (+6.1%) lagging revenue — margin compression at the edges",
            "FCF flat YoY (-1.7%)",
            "SBC at 8.1% of revenue",
        ],
    },
    "forecast_drivers": [
        "Whether genAI tools erode seat counts or get monetized (Firefly attach)",
        "Multiple re-rating from decline-pricing (fwd P/E 9.2) if growth persists",
    ],
    "scenarios": [
        {"name": "bear", "probability": 0.30, "eps": 15.00, "valuation_multiple": 8.0},
        {"name": "base", "probability": 0.50, "eps": 19.50, "valuation_multiple": 13.0},
        {"name": "bull", "probability": 0.20, "eps": 23.00, "valuation_multiple": 17.0},
    ],
    "investment_thesis": {
        "summary": "At 8.7× FCF and a 9.2× forward P/E, Adobe is priced like "
                   "a terminal-decline asset while still compounding "
                   "revenue at 12.7% and beating every quarter. The gap "
                   "between priced-in (-10.4%/yr) and consensus (+5.2%) is "
                   "the opportunity; the genAI tail risk is why it exists "
                   "and why the bear scenario carries 30%.",
        "why_market_may_be_wrong": [
            "Disruption pricing assumes zero monetization of AI features by the incumbent with the distribution and the enterprise contracts",
        ],
        "what_is_already_priced_in": [
            "A five-year cash-flow decline: the reverse DCF requirement is negative",
        ],
        "catalysts": [
            "Earnings 2026-09-10: net-new ARR and AI-product disclosure",
            "Buyback shrinking the share count against a single-digit multiple",
        ],
        "risks": [
            "GenAI substitution at the prosumer edge is real and measurable in web traffic before it reaches filings",
            "Three insiders sold ($19.3M) vs one small buy — insiders are not signaling the bottom",
        ],
        "invalidation_conditions": [
            "Revenue growth below 8% for two consecutive quarters",
            "A first-ever miss after the current beat streak",
            "FCF margin below 35%",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": "Every disrupted software franchise looked "
            "statistically cheap the whole way down. The filed numbers lag "
            "the substitution front line; flat FCF and decelerating "
            "operating income may be the first visible cracks, and 8× "
            "earnings on declining EPS is not cheap.",
        "fragile_assumptions": [
            "Base case assumes 13× on ~$19.50 EPS — requires the decline narrative to merely soften, not reverse",
            "Beat streak is management-guided; small beats say little about the 3-year trajectory",
        ],
        "accounting_concerns": [
            "SBC 8.1% of revenue dilutes the quality of the FCF yield",
        ],
        "valuation_concerns": [
            "P/E percentile of 0 vs own history is regime-confounded — the whole sector de-rated",
        ],
        "unresolved_questions": [
            "Creative vs Document vs Experience segment growth split (segment parsing pending)",
        ],
    },
    "conclusion": {"classification": "WATCH", "conviction": "MEDIUM",
                   "risk_reward_score": 60},
    "claims": [
        {"claim": "TTM revenue is $25.2B (+12.7% YoY) with an 89.4% gross margin and $10.3B FCF (40.8% margin).",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0000796343-26-000112", "SEC:ACCESSION:0000796343-26-000056"]},
        {"claim": "The current price implies a -10.4%/yr 5-year cash-flow decline (reverse DCF), while consensus expects +5.2% next-FY revenue growth.",
         "classification": "FACT",
         "source_ids": ["YAHOO:CHART:ADBE", "FMP:ESTIMATES:ADBE"]},
        {"claim": "The de-rating reflects generative-AI substitution fear rather than reported deterioration.",
         "classification": "INFERENCE",
         "source_ids": ["SEC:ACCESSION:0000796343-26-000112", "YAHOO:CHART:ADBE"]},
        {"claim": "Probability-weighted 12-month value is modestly above the current price; the wide bear case caps conviction.",
         "classification": "FORECAST", "source_ids": []},
    ],
}

SPECS["CRM"] = {
    "business_assessment": {
        "summary": "Enterprise SaaS platform at a 10.9% FCF yield after a "
                   "38.7% one-year decline on AI-agents-replace-seats "
                   "fear. Price-implied growth is -5.6%/yr against +9.8% "
                   "delivered. Uniquely in this set, TWO insiders made "
                   "discretionary open-market purchases with zero "
                   "discretionary sales. Consensus data is plan-gated for "
                   "this symbol, so the expectations component is absent.",
    },
    "fundamental_trend": {
        "direction": "IMPROVING", "strength": "MODERATE",
        "primary_drivers": [
            "EPS +52.2% YoY; operating income +20.9% on +13.3% revenue — the margin story is delivering",
            "FCF $14.7B (34.2% margin), FCF/NI 1.83",
            "Incremental operating margin 30.6%",
        ],
        "primary_deteriorations": [
            "Receivables growing 3.4pt faster than revenue",
            "Net debt $27.4B post-acquisitions; current ratio 0.79",
        ],
    },
    "forecast_drivers": [
        "Agentic-AI products cannibalizing vs expanding seat economics",
        "FCF/share (~$17.90) against a 9.1× P/FCF entry",
    ],
    "scenarios": [
        {"name": "bear", "probability": 0.30, "fair_value": 112.00},
        {"name": "base", "probability": 0.50, "fair_value": 190.00},
        {"name": "bull", "probability": 0.20, "fair_value": 286.00},
    ],
    "investment_thesis": {
        "summary": "The market prices seat-model decay (-5.6%/yr required) "
                   "while margins expand and cash conversion runs at 1.8×. "
                   "At 9.1× FCF, the base case needs only stability, not "
                   "acceleration. Insider buying — the only multi-buyer "
                   "signal among the four names analyzed today besides "
                   "NKE — supports the stabilization read.",
        "why_market_may_be_wrong": [
            "Agent pricing (consumption/outcome-based) can replace seat pricing accretively for the incumbent that owns the customer data layer",
        ],
        "what_is_already_priced_in": [
            "Mid-single-digit perpetual decline in cash generation",
        ],
        "catalysts": [
            "Next earnings (date unavailable — consensus plan-gated): cRPO growth and agent-product attach",
            "Buybacks at a 100 capital-allocation score against a depressed multiple",
        ],
        "risks": [
            "Receivables outgrowing revenue is an early demand-quality flag",
            "Seat-count exposure is real: headcount-linked pricing shrinks if customers' own headcount shrinks",
            "$27.4B net debt limits downside flexibility",
        ],
        "invalidation_conditions": [
            "Revenue growth below 8% for two consecutive quarters",
            "Operating margin expansion stalling below 20%",
            "Receivables-revenue gap widening past 10pt",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": "SaaS seat pricing is the disrupted layer, "
            "and CRM's growth was already decelerating (3-yr CAGR 9.8% < "
            "current 13.3% is mix/price, not volume). If agents compress "
            "seats faster than consumption pricing ramps, the 34% FCF "
            "margin is harvest-mode economics on a shrinking base — a "
            "value trap with leverage.",
        "fragile_assumptions": [
            "Base fair value assumes ~$19 FCF/share at 10× — a multiple the market may deny a decelerating asset",
            "No consensus data for this symbol: the expectations leg of the thesis is unverifiable on the current plan",
        ],
        "accounting_concerns": [
            "SBC 8.3% of revenue; receivables gap +3.4pt",
            "A known fiscal-label anomaly exists in one normalized Q4 period (labeled FY2025-Q4 amid FY2026 quarters) — data-quality event, does not affect amounts",
        ],
        "valuation_concerns": [
            "P/E percentile 0 vs own history is sector-regime-confounded",
        ],
        "unresolved_questions": [
            "cRPO/bookings trends require transcript and segment data (plan-gated/pending)",
        ],
    },
    "conclusion": {"classification": "ATTRACTIVE", "conviction": "MEDIUM",
                   "risk_reward_score": 64},
    "claims": [
        {"claim": "TTM revenue is $42.8B (+13.3% YoY); EPS grew 52.2%; FCF is $14.7B at a 34.2% margin.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0001108524-26-000127", "SEC:ACCESSION:0001108524-26-000060"]},
        {"claim": "The current price implies -5.6%/yr cash-flow decline for five years (reverse DCF) vs +9.8% delivered 3-year revenue CAGR.",
         "classification": "FACT",
         "source_ids": ["YAHOO:CHART:CRM", "SEC:ACCESSION:0001108524-26-000127"]},
        {"claim": "Two insiders made discretionary open-market purchases ($1.0M total) with zero discretionary sales in the trailing 6 months.",
         "classification": "FACT", "source_ids": ["SEC:FORM4:CRM"]},
        {"claim": "Margin expansion plus insider buying suggests stabilization rather than decay.",
         "classification": "INFERENCE",
         "source_ids": ["SEC:ACCESSION:0001108524-26-000127", "SEC:FORM4:CRM"]},
        {"claim": "Probability-weighted 12-month value is above the current price under 30/50/20 weights.",
         "classification": "FORECAST", "source_ids": []},
    ],
}

SPECS["NKE"] = {
    "business_assessment": {
        "summary": "Global athletic brand three years into a revenue decline "
                   "(-3.2% CAGR), down 43.8% in twelve months, now at "
                   "0.78× EV/revenue with FOUR distinct insiders making "
                   "discretionary open-market purchases and zero selling. "
                   "DATA CAVEATS stated plainly: NKE does not tag an "
                   "operating-income subtotal (operating margin and ROIC "
                   "unavailable), and the huge EPS 'beats' (+554%, +80%) "
                   "are percentage artifacts off near-zero estimates — the "
                   "expectations score of 100 overstates them.",
    },
    "fundamental_trend": {
        "direction": "MIXED", "strength": "WEAK",
        "primary_drivers": [
            "Balance sheet nearly net-cash (net debt $0.4B); financial-health score 98.6",
            "FCF recovered to $2.2B (+313% off a depressed base)",
            "Gross margin holding at 42.9% through the reset — brand pricing power intact",
        ],
        "primary_deteriorations": [
            "Revenue declining for three years; latest quarter $11.0B (-2.7% QoQ)",
            "Receivables grew 26.9pt faster than revenue — the widest gap in the coverage set; channel-inventory risk",
        ],
    },
    "forecast_drivers": [
        "Whether FY2027 marks revenue stabilization (consensus: -1.4% next FY — still declining)",
        "Normalized EPS power (~$2.40-3.20) vs the depressed $2.10 TTM",
    ],
    "scenarios": [
        {"name": "bear", "probability": 0.25, "eps": 1.70, "valuation_multiple": 15.0},
        {"name": "base", "probability": 0.50, "eps": 2.50, "valuation_multiple": 18.0},
        {"name": "bull", "probability": 0.25, "eps": 3.30, "valuation_multiple": 22.0},
    ],
    "investment_thesis": {
        "summary": "A turnaround bet, not a compounder story: the brand's "
                   "gross margin is intact, the balance sheet is clean, "
                   "and four insiders are buying a 0.78× EV/revenue price. "
                   "But revenue is still falling and the receivables build "
                   "is a genuine warning. Probability-weighted value sits "
                   "modestly above the price with a wide distribution — "
                   "WATCH, low conviction, insider-validated.",
        "why_market_may_be_wrong": [
            "Insiders with the best channel visibility are buying into the decline — historically the most informative insider configuration",
        ],
        "what_is_already_priced_in": [
            "Continued decline: price-implied growth (+3.6%) exceeds achieved (-3.2%) by ~7pt, so a modest turnaround IS required — this is not a zero-expectations entry",
        ],
        "catalysts": [
            "Earnings 2026-09-29: inventory/receivables cleanup and FY guidance",
            "Any quarter of positive constant-currency revenue growth",
        ],
        "risks": [
            "Receivables +26.9pt vs revenue — if this is channel stuffing, the next guide is down, not up",
            "Structural share loss to performance-running upstarts may not mean-revert",
        ],
        "invalidation_conditions": [
            "Receivables-revenue gap above 20pt again next quarter",
            "Gross margin below 40%",
            "A guidance cut for FY2027",
        ],
    },
    "adversarial_review": {
        "strongest_bear_case": "The receivables build says the sell-in is "
            "running ahead of sell-through: reported revenue may be "
            "borrowing from next year. If gross margin cracks under the "
            "markdowns that follow, the 'cheap' 0.78× EV/revenue re-bases "
            "on lower revenue AND lower margin, and normalized EPS is "
            "$1.70, not $2.50 — making today's price fair, not cheap.",
        "fragile_assumptions": [
            "Base case assumes EPS normalization to $2.50 without a demand recovery in filed evidence yet",
            "The expectations score (100) is an artifact of near-zero estimate bases and should be discounted entirely",
        ],
        "accounting_concerns": [
            "Receivables growth vs revenue (+26.9pt) is the set's largest divergence",
            "No operating-income tag — margin structure below gross is not independently verifiable from XBRL",
        ],
        "valuation_concerns": [
            "EV/revenue lows are only meaningful if revenue has bottomed — unproven",
        ],
        "unresolved_questions": [
            "Geographic and DTC-vs-wholesale split (segment parsing pending)",
        ],
    },
    "conclusion": {"classification": "WATCH", "conviction": "LOW",
                   "risk_reward_score": 55},
    "claims": [
        {"claim": "TTM revenue is $46.4B with a -3.2% 3-year CAGR; gross margin is 42.9%; net debt is $0.4B.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0000320187-26-000088", "SEC:ACCESSION:0000320187-26-000037"]},
        {"claim": "Four distinct insiders made discretionary open-market purchases totaling $2.7M in the trailing 6 months, with zero discretionary sales.",
         "classification": "FACT", "source_ids": ["SEC:FORM4:NKE"]},
        {"claim": "Receivables grew 26.9 points faster than revenue YoY — the widest gap in the coverage universe.",
         "classification": "FACT",
         "source_ids": ["SEC:ACCESSION:0000320187-26-000088"]},
        {"claim": "The insider cluster suggests management sees stabilization the filings don't yet show.",
         "classification": "INFERENCE", "source_ids": ["SEC:FORM4:NKE"]},
        {"claim": "Probability-weighted 12-month value is modestly above the current price with a wide distribution.",
         "classification": "FORECAST", "source_ids": []},
    ],
}


sys.path.insert(0, str(Path(__file__).resolve().parent))
from specs_extra import SPECS_EXTRA          # noqa: E402
from specs_extra2 import SPECS_EXTRA2        # noqa: E402
from specs_extra3 import SPECS_EXTRA3        # noqa: E402

SPECS.update(SPECS_EXTRA)
SPECS.update(SPECS_EXTRA2)
SPECS.update(SPECS_EXTRA3)
from specs_repass1 import REPASS               # noqa: E402
SPECS.update(REPASS)  # 2026-08-07 re-passes override earlier specs


if __name__ == "__main__":
    tickers = sys.argv[1:] or list(SPECS)
    for t in tickers:
        save(build_report(t, SPECS[t]))
