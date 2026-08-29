"""Versioned, read-optimized API contract for AI agents and external clients.

The existing dashboard API is UI-oriented and intentionally exposes many small
resources.  This router adds a compact, stable contract for agents: one stock
research packet, a bearish-opportunity scanner, and deterministic option
strategy guidance.  It never writes data or places trades.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from . import db
from .bundle import build_bundle
from .config import DATA_DIR
from .prediction import MODEL_VERSION

router = APIRouter(prefix="/api/v1", tags=["agent-api-v1"])

API_VERSION = "1.0.0"
COVERAGE_SNAPSHOT = DATA_DIR / "coverage_snapshot.json"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _pct_change(target: float | None, spot: float | None) -> float | None:
    if target is None or spot is None or spot <= 0:
        return None
    return round((float(target) / float(spot) - 1.0) * 100.0, 2)


def _scenario(report: dict[str, Any] | None, name: str) -> dict[str, Any] | None:
    if not report:
        return None
    wanted = name.strip().lower()
    for row in report.get("scenarios") or []:
        if str(row.get("name", "")).strip().lower() == wanted:
            return row
    return None


def bearish_asymmetry_score(
    *,
    expected_return_pct: float | None,
    bear_downside_pct: float | None,
    bull_upside_pct: float | None,
    quality_score: float | None,
    classification: str | None,
) -> float | None:
    """Heuristic 0-100 bearish *asymmetry* score, never a probability.

    The score rewards negative expected return, deep modeled downside and a
    low/negative modeled bull-case ceiling.  Lower quality adds only a small
    fragility term so valuation/forecast asymmetry remains the main driver.
    """
    if expected_return_pct is None or bear_downside_pct is None:
        return None

    negative_er = _clamp((-float(expected_return_pct)) / 40.0) * 40.0
    downside = _clamp((-float(bear_downside_pct)) / 60.0) * 30.0

    # Full bull-ceiling credit when even the bull case is at/below spot;
    # progressively less credit as modeled upside approaches +25%.
    bull = 0.0 if bull_upside_pct is None else float(bull_upside_pct)
    bull_ceiling = _clamp((25.0 - bull) / 25.0) * 20.0

    quality = 70.0 if quality_score is None else float(quality_score)
    fragility = _clamp((70.0 - quality) / 40.0) * 5.0
    label = 5.0 if str(classification or "").upper() == "UNATTRACTIVE" else 0.0
    return round(_clamp(negative_er + downside + bull_ceiling + fragility + label,
                        0.0, 100.0), 1)


def _forecast_expected_return(report: dict[str, Any] | None, key: str) -> float | None:
    try:
        value = (report or {})["forecasts"][key].get("expected_return_pct")
    except (KeyError, TypeError, AttributeError):
        return None
    return None if value is None else float(value)


def _prediction_horizon(prediction: dict[str, Any] | None, horizon: str) -> dict[str, Any]:
    if not prediction:
        return {}
    canonical = prediction.get("forecast_distribution") or {}
    row = (canonical.get("horizons") or {}).get(horizon)
    if row:
        return row
    return (prediction.get("horizons") or {}).get(horizon) or {}


def bear_strategy_guidance(
    *,
    expected_return_12m_pct: float | None,
    expected_return_3m_pct: float | None,
    bear_downside_pct: float | None,
    bull_upside_pct: float | None,
    prob_down_20pct: float | None,
) -> dict[str, Any]:
    """Map a bearish forecast shape to an option-expression *template*.

    This does not choose contracts.  Live contract selection remains in the
    existing options generator/scanner where IV, skew, liquidity and quotes
    can be evaluated.
    """
    if expected_return_12m_pct is None:
        return {
            "primary": "NO_RECOMMENDATION",
            "reason": "missing 12-month expected return",
            "contract_selection_required": True,
        }

    er12 = float(expected_return_12m_pct)
    er3 = None if expected_return_3m_pct is None else float(expected_return_3m_pct)
    downside = None if bear_downside_pct is None else float(bear_downside_pct)
    bull = None if bull_upside_pct is None else float(bull_upside_pct)
    p20 = None if prob_down_20pct is None else float(prob_down_20pct)

    if er12 >= 0 or (downside is not None and downside > -15):
        return {
            "primary": "WATCH",
            "reason": "12-month forecast lacks sufficient bearish asymmetry",
            "contract_selection_required": True,
        }

    primary = "BEAR_PUT_SPREAD"
    reason = (
        "defined-risk vertical matches a 6-12 month bearish valuation range "
        "while reducing long-put theta and premium"
    )
    alternatives: list[dict[str, str]] = []

    # Delayed bearish thesis: near term roughly flat, long horizon strongly down.
    if er3 is not None and er3 > -3.0 and er12 <= -15.0:
        alternatives.append({
            "strategy": "PUT_CALENDAR",
            "when": "only if the near-term thesis is neutral and front IV is rich",
        })
        alternatives.append({
            "strategy": "PUT_DIAGONAL",
            "when": "slow-grind decline with active short-put management",
        })

    # Crash convexity is a satellite, not the primary expression.
    if ((p20 is not None and p20 >= 0.40)
            or (downside is not None and downside <= -45.0)):
        alternatives.append({
            "strategy": "LONG_PUT",
            "when": "small tail-risk sleeve when a sharp decline/volatility expansion is expected",
        })

    if bull is not None and bull > 30.0:
        alternatives.append({
            "strategy": "AVOID_NAKED_SHORT",
            "when": "wide modeled upside makes unlimited-loss short stock unattractive",
        })

    return {
        "primary": primary,
        "reason": reason,
        "structure_rules": {
            "target_dte_days": "270-365 for a 6-12 month thesis",
            "long_put_delta": "approximately 0.50-0.65",
            "short_put_strike": "near p25/base-bear value or roughly 20-30% below spot",
            "max_debit_fraction_of_width": 0.50,
            "prefer_defined_risk": True,
            "stage_entries": True,
        },
        "alternatives": alternatives,
        "contract_selection_required": True,
        "next_step": "use /api/options/generate/{ticker} or /api/options/scan/{ticker} with live chain data",
    }


def _load_coverage_rows() -> tuple[list[dict[str, Any]], str | None]:
    """Prefer the persisted fast snapshot; fall back to a live DB/bundle read."""
    if COVERAGE_SNAPSHOT.exists():
        try:
            payload = json.loads(COVERAGE_SNAPSHOT.read_text(encoding="utf-8"))
            return list(payload.get("rows") or []), payload.get("generated_at")
        except Exception:
            pass

    conn = db.connect()
    try:
        companies = db.list_companies(conn)
        reports = db.latest_reports_map(conn)
    finally:
        conn.close()

    rows: list[dict[str, Any]] = []
    for company in companies:
        ticker = company["ticker"].upper()
        try:
            bundle = build_bundle(ticker)
        except Exception:
            continue
        report = reports.get(ticker) or {}
        fc12 = (report.get("forecasts") or {}).get("twelve_month") or {}
        derived = bundle.get("derived_metrics") or {}
        valuation = derived.get("valuation") or {}
        growth = derived.get("growth") or {}
        rows.append({
            "ticker": ticker,
            "legal_name": company.get("legal_name"),
            "sector": company.get("sector"),
            "price": (bundle.get("market_snapshot") or {}).get("price"),
            "revenue_yoy_pct": growth.get("revenue_yoy_pct"),
            "fcf_yield_pct": valuation.get("fcf_yield_pct"),
            "pe_ttm": valuation.get("pe_ttm"),
            "composite_score": (bundle.get("fundamental_scores") or {}).get("composite_score"),
            "data_quality_status": (bundle.get("data_quality") or {}).get("status"),
            "report_12m": {
                "expected_return_pct": fc12.get("expected_return_pct"),
                "fair_value_low": fc12.get("fair_value_low"),
                "fair_value_high": fc12.get("fair_value_high"),
                "classification": (report.get("conclusion") or {}).get("classification"),
            } if fc12 else None,
        })
    return rows, None


def _latest_report_and_prediction(ticker: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    conn = db.connect()
    try:
        report = db.latest_report(conn, ticker)
        prediction = db.latest_prediction_forecast(conn, ticker)
    finally:
        conn.close()
    return report, prediction


@router.get("/meta")
def api_meta() -> dict[str, Any]:
    """Small machine-readable discovery document for AI clients."""
    return {
        "api_version": API_VERSION,
        "read_only": True,
        "trade_execution": False,
        "semantics": {
            "bear_probability": (
                "present only when a persisted analysis report explicitly provides "
                "a bear-scenario probability; it is not assumed calibrated"
            ),
            "bearish_asymmetry_score": (
                "0-100 deterministic ranking heuristic; explicitly not a probability"
            ),
        },
        "routes": {
            "stock_research": "/api/v1/stocks/{ticker}/research",
            "bearish_opportunities": "/api/v1/opportunities/bearish",
            "bear_plan": "/api/v1/stocks/{ticker}/bear-plan",
            "legacy_live_options": "/api/options/generate/{ticker}",
            "legacy_option_scan": "/api/options/scan/{ticker}",
            "system_kpis": "/api/kpis",
            "data_quality": "/api/data-quality",
        },
    }


@router.get("/stocks/{ticker}/research")
def stock_research(
    ticker: str,
    include_live_quote: bool = Query(False, description="Query IBKR market data when available"),
) -> dict[str, Any]:
    """One-call, agent-friendly research packet for a stock."""
    symbol = ticker.upper().strip()
    try:
        bundle = build_bundle(symbol)
    except ValueError as exc:
        raise HTTPException(404, str(exc))

    report, prediction = _latest_report_and_prediction(symbol)
    market = bundle.get("market_snapshot") or {}
    spot = market.get("price")
    fc12 = ((report or {}).get("forecasts") or {}).get("twelve_month") or {}
    bear = _scenario(report, "bear")
    bull = _scenario(report, "bull")

    fair_low = fc12.get("fair_value_low")
    fair_high = fc12.get("fair_value_high")
    if fair_low is None and bear:
        fair_low = bear.get("fair_value")
    if fair_high is None and bull:
        fair_high = bull.get("fair_value")

    bear_downside = _pct_change(fair_low, spot)
    bull_upside = _pct_change(fair_high, spot)
    er12 = fc12.get("expected_return_pct")
    quality = (bundle.get("fundamental_scores") or {}).get("composite_score")
    classification = ((report or {}).get("conclusion") or {}).get("classification")
    pred12 = _prediction_horizon(prediction, "12m")
    pred3 = _prediction_horizon(prediction, "3m")
    er3 = _forecast_expected_return(report, "three_month")

    live_quote: dict[str, Any] | None = None
    if include_live_quote:
        try:
            from .market_data import get_provider

            provider = get_provider()
            try:
                live_quote = {"status": "ok", **provider.quote_underlying(symbol).model_dump(mode="json")}
            finally:
                provider.close()
        except Exception as exc:
            live_quote = {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}

    return {
        "api_version": API_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ticker": symbol,
        "company": bundle.get("company") or {},
        "market_snapshot": market,
        "live_quote": live_quote,
        "data_quality": bundle.get("data_quality") or {},
        "fundamentals": {
            "derived_metrics": bundle.get("derived_metrics") or {},
            "fundamental_scores": bundle.get("fundamental_scores") or {},
            "price_implied_expectations": bundle.get("price_implied_expectations") or {},
            "insider_activity": bundle.get("insider_activity") or {},
            "base_rates": bundle.get("base_rates") or {},
            "peer_group": bundle.get("peer_group") or {},
        },
        "analysis": {
            "report_available": report is not None,
            "forecasts": (report or {}).get("forecasts") or {},
            "scenarios": (report or {}).get("scenarios") or [],
            "investment_thesis": (report or {}).get("investment_thesis") or {},
            "adversarial_review": (report or {}).get("adversarial_review") or {},
            "conclusion": (report or {}).get("conclusion") or {},
        },
        "model_distribution": {
            "status": (prediction or {}).get("status", "MISSING") if prediction else "MISSING",
            "model_version": (prediction or {}).get("model_version", MODEL_VERSION),
            "three_month": pred3,
            "twelve_month": pred12,
        },
        "catalysts": bundle.get("catalyst_calendar") or {},
        "decision_context": {
            "expected_return_12m_pct": er12,
            "bear_fair_value": fair_low,
            "bull_fair_value": fair_high,
            "bear_downside_pct": bear_downside,
            "bull_upside_pct": bull_upside,
            "bear_probability": None if not bear else bear.get("probability"),
            "bear_probability_source": None if not bear else "analysis_scenario",
            "bear_probability_calibrated": False if bear else None,
            "prob_down_20pct_model": pred12.get("prob_down_20pct"),
            "quality_score": quality,
            "classification": classification,
            "bearish_asymmetry_score": bearish_asymmetry_score(
                expected_return_pct=er12,
                bear_downside_pct=bear_downside,
                bull_upside_pct=bull_upside,
                quality_score=quality,
                classification=classification,
            ),
            "bear_strategy_guidance": bear_strategy_guidance(
                expected_return_12m_pct=er12,
                expected_return_3m_pct=er3,
                bear_downside_pct=bear_downside,
                bull_upside_pct=bull_upside,
                prob_down_20pct=pred12.get("prob_down_20pct"),
            ),
        },
        "links": {
            "raw_bundle": f"/api/bundle/{symbol}",
            "report": f"/api/report/{symbol}",
            "forecast": f"/api/predict/{symbol}",
            "quote": f"/api/quote/{symbol}",
            "bear_plan": f"/api/v1/stocks/{symbol}/bear-plan",
            "option_expirations": f"/api/options/expirations/{symbol}",
        },
    }


@router.get("/opportunities/bearish")
def bearish_opportunities(
    max_expected_return_pct: float = Query(0.0, description="Include forecasts at or below this value"),
    min_asymmetry_score: float = Query(0.0, ge=0.0, le=100.0),
    sector: str | None = None,
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """Rank negative-expected-return stocks by deterministic bearish asymmetry."""
    rows, snapshot_generated_at = _load_coverage_rows()
    conn = db.connect()
    try:
        reports = db.latest_reports_map(conn)
    finally:
        conn.close()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        if sector and str(row.get("sector") or "").lower() != sector.lower():
            continue
        report12 = row.get("report_12m") or {}
        er12 = report12.get("expected_return_pct")
        if er12 is None or float(er12) > max_expected_return_pct:
            continue
        price = row.get("price")
        fair_low = report12.get("fair_value_low")
        fair_high = report12.get("fair_value_high")
        bear_downside = _pct_change(fair_low, price)
        bull_upside = _pct_change(fair_high, price)
        classification = report12.get("classification")
        score = bearish_asymmetry_score(
            expected_return_pct=float(er12),
            bear_downside_pct=bear_downside,
            bull_upside_pct=bull_upside,
            quality_score=row.get("composite_score"),
            classification=classification,
        )
        if score is None or score < min_asymmetry_score:
            continue

        report = reports.get(str(row.get("ticker") or "").upper()) or {}
        bear = _scenario(report, "bear")
        candidates.append({
            "ticker": row.get("ticker"),
            "legal_name": row.get("legal_name"),
            "sector": row.get("sector"),
            "price": price,
            "expected_return_12m_pct": er12,
            "bear_fair_value": fair_low,
            "bull_fair_value": fair_high,
            "bear_downside_pct": bear_downside,
            "bull_upside_pct": bull_upside,
            "quality_score": row.get("composite_score"),
            "classification": classification,
            "bear_probability": None if not bear else bear.get("probability"),
            "bear_probability_source": None if not bear else "analysis_scenario",
            "bear_probability_calibrated": False if bear else None,
            "bearish_asymmetry_score": score,
            "data_quality_status": row.get("data_quality_status"),
        })

    candidates.sort(
        key=lambda r: (
            r["bearish_asymmetry_score"],
            -float(r["expected_return_12m_pct"]),
        ),
        reverse=True,
    )
    return {
        "api_version": API_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage_snapshot_generated_at": snapshot_generated_at,
        "probability_semantics": (
            "bear_probability is emitted only from an explicit analysis-scenario "
            "probability and is not assumed calibrated; bearish_asymmetry_score "
            "is a ranking score, not a probability"
        ),
        "filters": {
            "max_expected_return_pct": max_expected_return_pct,
            "min_asymmetry_score": min_asymmetry_score,
            "sector": sector,
            "limit": limit,
        },
        "count": min(len(candidates), limit),
        "candidates": candidates[:limit],
    }


@router.get("/stocks/{ticker}/bear-plan")
def stock_bear_plan(ticker: str) -> dict[str, Any]:
    """Compact bearish trade-expression context without selecting contracts."""
    symbol = ticker.upper().strip()
    try:
        bundle = build_bundle(symbol)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    report, prediction = _latest_report_and_prediction(symbol)
    market = bundle.get("market_snapshot") or {}
    spot = market.get("price")
    fc12 = ((report or {}).get("forecasts") or {}).get("twelve_month") or {}
    bear = _scenario(report, "bear")
    bull = _scenario(report, "bull")
    fair_low = fc12.get("fair_value_low") if fc12 else None
    fair_high = fc12.get("fair_value_high") if fc12 else None
    if fair_low is None and bear:
        fair_low = bear.get("fair_value")
    if fair_high is None and bull:
        fair_high = bull.get("fair_value")
    er12 = fc12.get("expected_return_pct") if fc12 else None
    er3 = _forecast_expected_return(report, "three_month")
    p12 = _prediction_horizon(prediction, "12m")
    downside = _pct_change(fair_low, spot)
    upside = _pct_change(fair_high, spot)

    return {
        "api_version": API_VERSION,
        "ticker": symbol,
        "spot": spot,
        "expected_return_12m_pct": er12,
        "bear_fair_value": fair_low,
        "bull_fair_value": fair_high,
        "bear_downside_pct": downside,
        "bull_upside_pct": upside,
        "bear_probability": None if not bear else bear.get("probability"),
        "bear_probability_calibrated": False if bear else None,
        "prob_down_20pct_model": p12.get("prob_down_20pct"),
        "strategy": bear_strategy_guidance(
            expected_return_12m_pct=er12,
            expected_return_3m_pct=er3,
            bear_downside_pct=downside,
            bull_upside_pct=upside,
            prob_down_20pct=p12.get("prob_down_20pct"),
        ),
        "live_contract_routes": {
            "expirations": f"/api/options/expirations/{symbol}",
            "strikes": f"/api/options/strikes/{symbol}?month={{YYYYMM}}",
            "generate": f"/api/options/generate/{symbol}?month={{YYYYMM}}&strikes={{csv}}",
            "scan": f"/api/options/scan/{symbol}?month={{YYYYMM}}&strategy=bear_put_spread&strikes={{csv}}&objective=expected_value&defined_risk=true&horizon=12m",
        },
    }
