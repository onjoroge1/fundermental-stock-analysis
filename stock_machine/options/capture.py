"""Shared bounded collection using server-owned provider and database settings."""
from __future__ import annotations
import math
from stock_machine import db
from stock_machine.options.surface_features import extract_surface
from stock_machine.options.surface_store import history, save


def meaningful(surface: dict) -> bool:
    iv = (surface.get("features") or {}).get("atm_iv")
    return (surface.get("status") == "OK" and isinstance(iv, (int, float))
            and not isinstance(iv, bool) and math.isfinite(iv) and iv > 0)


def capture_ticker(provider, ticker: str, max_expiries: int = 2,
                   max_strikes: int = 18) -> dict:
    if not 1 <= max_expiries <= 2 or not 1 <= max_strikes <= 18:
        raise ValueError("capture bounds exceeded")
    underlying = provider.resolve_underlying(ticker)
    months = list(underlying.option_months or [])[:max_expiries]
    if not months:
        raise RuntimeError("provider returned no option months")
    quote = provider.quote_underlying(ticker)
    spot = quote.mark if quote.mark is not None and quote.mark > 0 else (
        (quote.bid + quote.ask) / 2
        if quote.bid is not None and quote.ask is not None and quote.ask >= quote.bid
        else quote.last)
    if spot is None or spot <= 0:
        raise RuntimeError("underlying quote has no usable spot price")

    chains = []
    for month in months:
        strikes = provider.available_strikes(ticker, month)
        ladder = sorted(set(strikes.call_strikes) | set(strikes.put_strikes),
                        key=lambda x: abs(x - spot))[:max_strikes]
        ladder = sorted(ladder)
        if not ladder:
            continue
        chains.append(provider.option_chain(ticker, month, ladder))
    if not chains:
        raise RuntimeError("no option chains captured")

    with db.connect() as conn:
        prior = history(conn, ticker, before_as_of=max(c.fetched_at for c in chains).isoformat())
    surface = extract_surface(chains, prior_surfaces=prior)
    if not meaningful(surface):
        raise RuntimeError(
            "captured chain has no usable at-the-money implied volatility"
        )
    with db.connect() as conn:
        snapshot_id = save(conn, surface)
    return {"ticker": ticker, "status": "ok", "snapshot_id": snapshot_id,
            "as_of": surface["as_of"], "features": surface["features"]}
