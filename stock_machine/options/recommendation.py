"""One-call, read-only option recommendation analysis.

This module discovers expirations/strikes, loads the persisted stock forecast,
queries live option chains, generates defined-risk verticals, evaluates a
single long option as a convexity alternative, and conditionally evaluates
calendar/diagonal structures behind the P2-E event gate.

It never places an order. Quantile-weighted payoff estimates are explicitly
coarse scenario integrations, not calibrated probability-of-profit claims.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from math import fabs
from typing import Any, Literal

from .. import db
from ..bundle import build_bundle
from ..forecasts import ForecastDistribution, from_prediction_lab
from ..market_data.models import OptionChainSnapshot, OptionQuote
from .extended import mixed_expiration
from .generator import GenerationPolicy, generate_strategies
from .models import StrategyCandidate, StrategyType
from .surface_features import extract_surface

HORIZON_DAYS = {"3m": 90, "6m": 180, "12m": 300}
REPORT_HORIZONS = {"3m": "three_month", "6m": "six_month", "12m": "twelve_month"}
QUANTILE_WEIGHTS = {"p10": 0.10, "p25": 0.20, "p50": 0.40, "p75": 0.20, "p90": 0.10}


def _parse_yyyymmdd(value: str) -> date:
    value = str(value)
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def choose_expiration_month(
    months: list[dict], target_days: int, *, today: date | None = None,
    minimum_days: int = 21,
) -> dict | None:
    """Pick the standard expiry closest to target DTE, excluding near-expired rows."""
    today = today or date.today()
    candidates = []
    for row in months:
        standard = row.get("standard")
        month = row.get("month")
        if not standard or not month:
            continue
        try:
            expiry = _parse_yyyymmdd(str(standard))
        except (ValueError, IndexError):
            continue
        dte = (expiry - today).days
        if dte < minimum_days:
            continue
        candidates.append({**row, "expiration": expiry.isoformat(), "dte": dte})
    if not candidates:
        return None
    return min(candidates, key=lambda row: (abs(row["dte"] - target_days), row["dte"]))


def choose_strike_ladder(
    strikes: list[float], spot: float, *, anchors: list[float] | None = None,
    limit: int = 14,
) -> list[float]:
    """Bound the chain request while retaining ATM and thesis-target strikes."""
    usable = sorted({float(x) for x in strikes if x and float(x) > 0})
    if not usable or spot <= 0:
        return []
    anchors = [spot, *(anchors or [])]
    selected: set[float] = set()
    per_anchor = max(2, limit // max(1, len(anchors)))
    for anchor in anchors:
        if anchor is None or anchor <= 0:
            continue
        nearest = sorted(usable, key=lambda strike: (abs(strike - anchor), strike))[:per_anchor]
        selected.update(nearest)
    if len(selected) < limit:
        selected.update(sorted(usable, key=lambda strike: abs(strike - spot))[:limit])
    return sorted(selected, key=lambda strike: strike)[:limit]


def _stored_context(ticker: str) -> tuple[dict, dict, ForecastDistribution | None]:
    with db.connect() as conn:
        report = db.latest_report(conn, ticker) or {}
        prediction = db.latest_prediction_forecast(conn, ticker) or {}
    forecast: ForecastDistribution | None = None
    if prediction:
        try:
            canonical = prediction.get("forecast_distribution")
            forecast = (
                ForecastDistribution.model_validate(canonical)
                if canonical
                else from_prediction_lab(prediction)
            )
        except Exception:
            forecast = None
    return report, prediction, forecast


def _forecast_horizon(forecast: ForecastDistribution | None, target_days: int):
    if not forecast or not forecast.horizons:
        return None
    return min(forecast.horizons, key=lambda row: abs(row.horizon_days - target_days))


def _report_return(report: dict, horizon: str) -> float | None:
    key = REPORT_HORIZONS[horizon]
    row = ((report.get("forecasts") or {}).get(key) or {})
    value = row.get("expected_return_pct")
    return None if value is None else float(value)


def determine_direction(
    report: dict, forecast: ForecastDistribution | None, horizon: str,
    requested: str = "auto",
) -> Literal["bearish", "bullish", "neutral"]:
    requested = requested.lower()
    if requested in {"bearish", "bullish"}:
        return requested  # type: ignore[return-value]
    if requested != "auto":
        raise ValueError("direction must be auto, bearish, or bullish")
    expected = _report_return(report, horizon)
    if expected is None:
        row = _forecast_horizon(forecast, HORIZON_DAYS[horizon])
        expected = row.expected_return * 100.0 if row else None
    if expected is None or abs(expected) < 5.0:
        return "neutral"
    return "bullish" if expected > 0 else "bearish"


def _analysis_targets(ticker: str, report: dict, forecast: ForecastDistribution | None,
                      horizon: str) -> dict[str, Any]:
    bundle = build_bundle(ticker)
    spot = float((bundle.get("market_snapshot") or {}).get("price") or 0)
    report_row = ((report.get("forecasts") or {}).get(REPORT_HORIZONS[horizon]) or {})
    low = report_row.get("fair_value_low")
    high = report_row.get("fair_value_high")
    row = _forecast_horizon(forecast, HORIZON_DAYS[horizon])
    quantiles = row.price_quantiles.model_dump() if row else {}
    anchors = [
        float(value) for value in (
            low, high, quantiles.get("p25"), quantiles.get("p50"), quantiles.get("p75")
        ) if value is not None and float(value) > 0
    ]
    return {
        "spot": spot,
        "fair_value_low": low,
        "fair_value_high": high,
        "expected_return_pct": _report_return(report, horizon),
        "forecast": row.model_dump(mode="json") if row else None,
        "anchors": anchors,
    }


def _candidate_payload(candidate: StrategyCandidate) -> dict:
    return {
        "candidate_id": candidate.candidate_id,
        "strategy_type": candidate.strategy_type.value,
        "expiration": candidate.expiration.isoformat(),
        "days_to_expiration": candidate.days_to_expiration,
        "legs": [leg.model_dump(mode="json") for leg in candidate.legs],
        "payoff": candidate.payoff.model_dump(mode="json"),
        "liquidity": candidate.liquidity.model_dump(mode="json"),
        "forecast_alignment": candidate.forecast.model_dump(mode="json"),
        "ranking": candidate.ranking.model_dump(mode="json"),
        "position_greeks": candidate.position_greeks.model_dump(mode="json"),
        "warnings": candidate.warnings,
    }


def _long_option_candidate(
    chain: OptionChainSnapshot, forecast: ForecastDistribution | None,
    target_days: int, right: str,
) -> dict | None:
    spot = chain.underlying_quote.mark or chain.underlying_quote.last
    if spot is None:
        return None
    candidates = [
        option for option in chain.options
        if option.contract.right == right and option.quote.ask is not None and option.quote.ask > 0
    ]
    if not candidates:
        return None
    with_delta = [option for option in candidates if option.delta is not None]
    if with_delta:
        target_delta = -0.58 if right == "P" else 0.58
        option = min(with_delta, key=lambda item: abs(float(item.delta) - target_delta))
    else:
        option = min(candidates, key=lambda item: abs(item.contract.strike - float(spot)))
    premium = float(option.quote.ask)
    strike = float(option.contract.strike)
    multiplier = int(option.contract.multiplier)
    horizon = _forecast_horizon(forecast, target_days)
    scenario_rows: list[dict] = []
    expected_pnl = None
    positive_mass = None
    if horizon:
        prices = horizon.price_quantiles.model_dump()
        weighted = 0.0
        mass_total = 0.0
        positive = 0.0
        for key, weight in QUANTILE_WEIGHTS.items():
            price = prices.get(key)
            if price is None:
                continue
            intrinsic = max(strike - float(price), 0.0) if right == "P" else max(float(price) - strike, 0.0)
            pnl = (intrinsic - premium) * multiplier
            scenario_rows.append({"quantile": key, "price": price, "weight": weight, "pnl": round(pnl, 2)})
            weighted += pnl * weight
            mass_total += weight
            if pnl > 0:
                positive += weight
        if mass_total:
            expected_pnl = round(weighted / mass_total, 2)
            positive_mass = round(positive / mass_total, 3)
    max_loss = round(premium * multiplier, 2)
    breakeven = strike - premium if right == "P" else strike + premium
    return {
        "strategy_type": "long_put" if right == "P" else "long_call",
        "expiration": option.contract.expiration.isoformat(),
        "days_to_expiration": (option.contract.expiration - chain.fetched_at.date()).days,
        "contract": option.contract.model_dump(mode="json"),
        "entry_price": premium,
        "max_loss": max_loss,
        "breakeven": round(breakeven, 4),
        "expected_pnl_coarse": expected_pnl,
        "positive_quantile_mass": positive_mass,
        "scenario_rows": scenario_rows,
        "quote": option.quote.model_dump(mode="json"),
        "implied_volatility": option.implied_volatility,
        "delta": option.delta,
        "warning": "positive_quantile_mass is a coarse five-bucket scenario measure, not calibrated probability of profit",
    }


def _best_vertical(result, direction: str) -> StrategyCandidate | None:
    allowed = (
        {StrategyType.BEAR_PUT_DEBIT_SPREAD, StrategyType.BEAR_CALL_CREDIT_SPREAD}
        if direction == "bearish"
        else {StrategyType.BULL_CALL_DEBIT_SPREAD, StrategyType.BULL_PUT_CREDIT_SPREAD}
    )
    candidates = [c for c in result.candidates if c.strategy_type in allowed]
    return max(candidates, key=lambda c: c.ranking.total) if candidates else None


def _common_strike(a: list[float], b: list[float], target: float) -> float | None:
    common = sorted(set(float(x) for x in a) & set(float(x) for x in b))
    return min(common, key=lambda x: abs(x - target)) if common else None


def recommend(
    ticker: str, *, direction: str = "auto", horizon: str = "12m",
    capital: float | None = None, allow_delayed: bool = True,
) -> dict:
    symbol = ticker.upper().strip()
    if horizon not in HORIZON_DAYS:
        raise ValueError("horizon must be 3m, 6m, or 12m")
    report, legacy_prediction, forecast = _stored_context(symbol)
    resolved_direction = determine_direction(report, forecast, horizon, direction)
    context = _analysis_targets(symbol, report, forecast, horizon)
    if resolved_direction == "neutral":
        return {
            "status": "NO_TRADE",
            "ticker": symbol,
            "direction": "neutral",
            "horizon": horizon,
            "reason": "stored forecast lacks a >=5 percentage-point directional expected-return edge",
            "stock_context": context,
        }

    from ..market_data import get_provider
    provider = get_provider()
    try:
        expirations = provider.available_expirations(symbol)
        far = choose_expiration_month(
            expirations.get("months") or [], HORIZON_DAYS[horizon], minimum_days=30
        )
        if not far:
            raise ValueError("no suitable listed expiration")
        far_strikes = provider.available_strikes(symbol, far["month"])
        quote = provider.quote_underlying(symbol)
        spot = float(quote.mark or quote.last or context.get("spot") or 0)
        if spot <= 0:
            raise ValueError("no usable underlying price")
        ladder = choose_strike_ladder(
            far_strikes.put_strikes if resolved_direction == "bearish" else far_strikes.call_strikes,
            spot,
            anchors=context.get("anchors") or [],
            limit=14,
        )
        far_chain = provider.option_chain(symbol, far["month"], ladder)

        strategy_types = (
            {StrategyType.BEAR_PUT_DEBIT_SPREAD, StrategyType.BEAR_CALL_CREDIT_SPREAD}
            if resolved_direction == "bearish"
            else {StrategyType.BULL_CALL_DEBIT_SPREAD, StrategyType.BULL_PUT_CREDIT_SPREAD}
        )
        generated = generate_strategies(
            far_chain,
            forecast,
            GenerationPolicy(
                min_days_to_expiration=max(21, far["dte"] - 45),
                max_days_to_expiration=far["dte"] + 45,
                maximum_width=max(5.0, spot * 0.35),
                maximum_relative_spread=0.40,
                minimum_open_interest=10,
                capital_limit=capital,
                allow_delayed=allow_delayed,
                max_candidates=25,
                strategy_types=strategy_types,
            ),
        )
        vertical = _best_vertical(generated, resolved_direction)
        long_option = _long_option_candidate(
            far_chain, forecast, HORIZON_DAYS[horizon], "P" if resolved_direction == "bearish" else "C"
        )

        near = choose_expiration_month(expirations.get("months") or [], 45, minimum_days=21)
        mixed: list[dict] = []
        near_chain = None
        if near and near["month"] != far["month"]:
            try:
                near_strikes = provider.available_strikes(symbol, near["month"])
                near_ladder = choose_strike_ladder(
                    near_strikes.put_strikes if resolved_direction == "bearish" else near_strikes.call_strikes,
                    spot,
                    anchors=[spot * (0.88 if resolved_direction == "bearish" else 1.12)],
                    limit=10,
                )
                near_chain = provider.option_chain(symbol, near["month"], near_ladder)
                right = "P" if resolved_direction == "bearish" else "C"
                same = _common_strike(near_ladder, ladder, spot)
                diagonal_near_target = spot * (0.88 if right == "P" else 1.12)
                diag_near = min(near_ladder, key=lambda x: abs(x - diagonal_near_target)) if near_ladder else None
                diag_far = min(ladder, key=lambda x: abs(x - spot)) if ladder else None
                specs = []
                if same is not None:
                    specs.append(("put_calendar" if right == "P" else "call_calendar", same, same))
                if diag_near is not None and diag_far is not None:
                    specs.append(("put_diagonal" if right == "P" else "call_diagonal", diag_near, diag_far))
                for strategy_type, near_strike, far_strike in specs:
                    row = mixed_expiration(near_chain, far_chain, near_strike, far_strike, right=right)
                    if row.get("status") == "OK":
                        from ..events.screen import build_event_screen
                        with db.connect() as conn:
                            screen = build_event_screen(
                                conn, symbol, strategy_type,
                                row["near_expiration"], row["far_expiration"],
                            )
                        row["event_screen"] = screen
                        row["execution_eligible"] = screen.get("status") == "CLEAR"
                    mixed.append(row)
            except Exception as exc:
                mixed.append({
                    "status": "UNAVAILABLE",
                    "reason": f"mixed-expiration analysis unavailable: {type(exc).__name__}: {exc}",
                })

        chains_for_surface = [far_chain] if near_chain is None else [near_chain, far_chain]
        prior = []
        try:
            from .surface_store import history
            with db.connect() as conn:
                prior = history(conn, symbol, before_as_of=datetime.now(timezone.utc).isoformat())
        except Exception:
            prior = []
        surface = extract_surface(chains_for_surface, prior_surfaces=prior)
    finally:
        provider.close()

    primary = _candidate_payload(vertical) if vertical else None
    data_availability = sorted({
        option.quote.availability.value for option in far_chain.options
    })
    realtime = data_availability == ["realtime"]
    forecast_ready = bool(forecast and forecast.readiness_status == "VALIDATED")
    execution_ready = bool(primary and realtime and forecast_ready)
    legacy_12m = ((legacy_prediction.get("horizons") or {}).get("12m") or {}) if legacy_prediction else {}

    return {
        "status": "OK" if primary else "NO_OPTION_CLEARED",
        "ticker": symbol,
        "direction": resolved_direction,
        "horizon": horizon,
        "stock_context": context,
        "selected_expiration": far,
        "strike_ladder": ladder,
        "primary_policy": (
            "defined-risk vertical is the default expression; long option is a convexity alternative; "
            "calendars/diagonals are alternatives only when their event screen clears"
        ),
        "primary": primary,
        "long_option_alternative": long_option,
        "mixed_expiration_alternatives": mixed,
        "surface": surface,
        "tail_context": {
            "prob_down_20pct_model": legacy_12m.get("prob_down_20pct") if horizon == "12m" else None,
        },
        "execution_readiness": {
            "ready": execution_ready,
            "market_data_realtime": realtime,
            "forecast_validated": forecast_ready,
            "availability": data_availability,
            "reason": None if execution_ready else (
                "analysis only until a primary candidate exists, market data is realtime, and the forecast contract is VALIDATED"
            ),
        },
        "generation": {
            "candidate_count": len(generated.candidates),
            "rejected_count": len(generated.rejected),
            "warnings": generated.warnings,
        },
        "disclaimer": "Research recommendation only; no order is created or submitted.",
    }
