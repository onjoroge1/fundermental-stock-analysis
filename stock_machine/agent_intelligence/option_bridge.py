"""Bounded defined-risk option candidate bridge for Agent Intelligence v2.

Called only from PAPER intelligence. It may read current option market data, but
it never accesses an account, creates an order, or submits to a broker.
"""
from __future__ import annotations

from datetime import date

from ..options.generator import GenerationPolicy, generate_strategies
from ..options.models import StrategyType
from ..options.surface_features import extract_surface
from ..options.surface_store import history, save as save_surface


DIRECTION_TYPES = {
    "BULLISH": {
        StrategyType.BULL_CALL_DEBIT_SPREAD,
        StrategyType.BULL_PUT_CREDIT_SPREAD,
    },
    "BEARISH": {
        StrategyType.BEAR_PUT_DEBIT_SPREAD,
        StrategyType.BEAR_CALL_CREDIT_SPREAD,
    },
    "NEUTRAL": {StrategyType.IRON_CONDOR},
}


def _spot(quote) -> float:
    if quote.mark is not None and quote.mark > 0:
        return float(quote.mark)
    if quote.bid is not None and quote.ask is not None and quote.ask >= quote.bid:
        return float((quote.bid + quote.ask) / 2)
    if quote.last is not None and quote.last > 0:
        return float(quote.last)
    raise ValueError("OPTION_UNDERLYING_PRICE_UNAVAILABLE")


def _expiration(months: list[dict], *, target_dte: int = 45) -> dict:
    today = date.today()
    rows = []
    for row in months or []:
        standard = str(row.get("standard") or "")
        if len(standard) != 8 or not standard.isdigit() or not row.get("month"):
            continue
        expiry = date(int(standard[:4]), int(standard[4:6]), int(standard[6:8]))
        dte = (expiry - today).days
        if 21 <= dte <= 90:
            rows.append({**row, "expiration": expiry.isoformat(), "dte": dte})
    if not rows:
        raise ValueError("OPTION_EXPIRATION_21_90D_UNAVAILABLE")
    return min(rows, key=lambda x: (abs(x["dte"] - target_dte), x["dte"]))


def _nearest(values: list[float], spot: float, limit: int = 8) -> list[float]:
    return sorted(sorted({float(x) for x in values if float(x) > 0},
                         key=lambda x: (abs(x - spot), x))[:limit])


def generate(ticker: str, direction: str, *, max_risk_usd: float = 1000.0) -> dict:
    direction = str(direction or "").upper()
    types = DIRECTION_TYPES.get(direction)
    if not types:
        return {"status": "SKIPPED", "reason": "OPTION_DIRECTION_UNSUPPORTED",
                "candidates": [], "broker_submission": False}

    from ..market_data import get_provider
    from .. import db
    provider = get_provider()
    try:
        expirations = provider.available_expirations(ticker)
        chosen = _expiration(expirations.get("months") or [])
        quote = provider.quote_underlying(ticker)
        spot = _spot(quote)
        strikes = provider.available_strikes(ticker, chosen["month"])
        calls = _nearest(strikes.call_strikes, spot)
        puts = _nearest(strikes.put_strikes, spot)
        ladder = sorted(set(calls + puts))
        if len(ladder) < 4:
            raise ValueError("OPTION_STRIKE_LADDER_INSUFFICIENT")
        chain = provider.option_chain(ticker, chosen["month"], ladder)
        policy = GenerationPolicy(
            min_days_to_expiration=max(21, chosen["dte"] - 7),
            max_days_to_expiration=min(90, chosen["dte"] + 7),
            maximum_width=max(2.0, spot * 0.08),
            maximum_relative_spread=0.30,
            minimum_open_interest=50,
            maximum_quote_age_seconds=120,
            capital_limit=max_risk_usd,
            allow_delayed=False,
            max_candidates=20,
            max_combinations=3000,
            strategy_types=types,
        )
        generated = generate_strategies(chain, forecast=None, policy=policy)
        with db.connect() as conn:
            prior = history(conn, ticker, before_as_of=chain.fetched_at.isoformat())
        surface = extract_surface([chain], prior_surfaces=prior)
        surface_id = None
        if surface.get("status") == "OK":
            with db.connect() as conn:
                surface_id = save_surface(conn, surface)
        candidates = [c.model_dump(mode="json") for c in generated.candidates]
        return {
            "status": "OK" if candidates else "NO_CANDIDATE_CLEARED",
            "ticker": ticker,
            "direction": direction,
            "spot": spot,
            "expiration": chosen,
            "surface_snapshot_id": surface_id,
            "surface": surface,
            "candidates": candidates,
            "rejected_count": len(generated.rejected),
            "warnings": generated.warnings,
            "provider_calls_bounded": True,
            "broker_submission": False,
        }
    finally:
        provider.close()
