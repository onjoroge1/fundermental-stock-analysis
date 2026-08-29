"""Option-implied predictive features for P1 research.

Features are computed from observed option-chain snapshots only.  No missing IV
or open-interest field is synthesized.  Historical IV percentile is computed
only from previously persisted surface snapshots with as_of <= the current
snapshot time.
"""
from __future__ import annotations

from datetime import date
from math import sqrt
from statistics import mean

from ..market_data.models import OptionChainSnapshot, OptionQuote

FEATURE_NAMES = [
    "atm_iv",
    "iv_skew_25d",
    "term_slope",
    "expected_move_pct",
    "put_call_oi_ratio",
    "iv_percentile",
    "has_atm_iv",
    "has_skew",
    "has_term",
    "has_oi",
    "has_iv_history",
]


def _spot(chain: OptionChainSnapshot) -> float | None:
    q = chain.underlying_quote
    if q.mark is not None and q.mark > 0:
        return q.mark
    if q.bid is not None and q.ask is not None and q.ask >= q.bid and q.ask > 0:
        return (q.bid + q.ask) / 2
    if q.last is not None and q.last > 0:
        return q.last
    return None


def _nearest(items: list[OptionQuote], *, right: str, target_delta: float | None = None,
             target_strike: float | None = None) -> OptionQuote | None:
    candidates = [q for q in items if q.contract.right == right and q.implied_volatility is not None]
    if not candidates:
        return None
    if target_delta is not None:
        with_delta = [q for q in candidates if q.delta is not None]
        if with_delta:
            return min(with_delta, key=lambda q: abs(float(q.delta) - target_delta))
    if target_strike is not None:
        return min(candidates, key=lambda q: abs(q.contract.strike - target_strike))
    return None


def _one_expiry(chain: OptionChainSnapshot, today: date | None = None) -> dict:
    spot = _spot(chain)
    expiry = chain.options[0].contract.expiration if chain.options else None
    today = today or chain.fetched_at.date()
    dte = max(0, (expiry - today).days) if expiry else 0

    call_atm = _nearest(chain.options, right="C", target_strike=spot) if spot else None
    put_atm = _nearest(chain.options, right="P", target_strike=spot) if spot else None
    atm_ivs = [q.implied_volatility for q in (call_atm, put_atm)
               if q is not None and q.implied_volatility is not None]
    atm_iv = mean(atm_ivs) if atm_ivs else None

    put25 = _nearest(chain.options, right="P", target_delta=-0.25)
    call25 = _nearest(chain.options, right="C", target_delta=0.25)
    skew = None
    if put25 and call25 and put25.implied_volatility is not None and call25.implied_volatility is not None:
        skew = put25.implied_volatility - call25.implied_volatility

    put_oi = sum(float(q.open_interest or 0) for q in chain.options if q.contract.right == "P")
    call_oi = sum(float(q.open_interest or 0) for q in chain.options if q.contract.right == "C")
    oi_ratio = put_oi / call_oi if call_oi > 0 else None
    expected_move_pct = (atm_iv * sqrt(dte / 365.0) * 100.0
                         if atm_iv is not None and dte > 0 else None)
    return {
        "expiration": expiry.isoformat() if expiry else None,
        "dte": dte,
        "spot": spot,
        "atm_iv": atm_iv,
        "iv_skew_25d": skew,
        "put_call_oi_ratio": oi_ratio,
        "expected_move_pct": expected_move_pct,
    }


def _percentile(value: float, history: list[float]) -> float | None:
    if value is None or len(history) < 20:
        return None
    return sum(x <= value for x in history) / len(history)


def extract_surface(chains: list[OptionChainSnapshot],
                    prior_surfaces: list[dict] | None = None) -> dict:
    """Return one ticker-level surface from one or more expiration snapshots."""
    if not chains:
        return {"status": "PENDING", "reason": "no option-chain snapshots"}
    symbol = chains[0].underlying.symbol
    if any(c.underlying.symbol != symbol for c in chains):
        raise ValueError("all chains must belong to the same symbol")

    rows = sorted((_one_expiry(c) for c in chains), key=lambda r: r["dte"])
    usable = [r for r in rows if r["atm_iv"] is not None]
    near = usable[0] if usable else rows[0]
    far = usable[-1] if len(usable) >= 2 else None
    term_slope = (far["atm_iv"] - near["atm_iv"]
                  if far and near["atm_iv"] is not None else None)

    history = [float(r["atm_iv"]) for r in (prior_surfaces or [])
               if r.get("atm_iv") is not None]
    iv_pct = _percentile(near.get("atm_iv"), history)
    features = {
        "atm_iv": near.get("atm_iv"),
        "iv_skew_25d": near.get("iv_skew_25d"),
        "term_slope": term_slope,
        "expected_move_pct": near.get("expected_move_pct"),
        "put_call_oi_ratio": near.get("put_call_oi_ratio"),
        "iv_percentile": iv_pct,
        "has_atm_iv": float(near.get("atm_iv") is not None),
        "has_skew": float(near.get("iv_skew_25d") is not None),
        "has_term": float(term_slope is not None),
        "has_oi": float(near.get("put_call_oi_ratio") is not None),
        "has_iv_history": float(iv_pct is not None),
    }
    return {
        "status": "OK",
        "symbol": symbol,
        "as_of": max(c.fetched_at for c in chains).isoformat(),
        "provider": chains[0].provider,
        "near_expiration": near.get("expiration"),
        "expirations": rows,
        "features": features,
        "vector": [0.0 if features[n] is None else float(features[n]) for n in FEATURE_NAMES],
    }
