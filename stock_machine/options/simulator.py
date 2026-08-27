"""Multi-leg strategy builder and payoff simulator.

Builds named structures (jade lizard, spreads, condors, straddles...) from a
live chain, then reuses the exact piecewise-linear payoff engine in
payoff.py — no separate math, so the simulator and the generator can never
disagree.

Every structure is defined by its legs; nothing here is hard-coded per
strategy beyond leg selection. Analysis only: this module places no orders
and makes no recommendation.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..market_data.models import OptionChainSnapshot, OptionQuote
from .models import OptionAction, OptionLeg, PayoffPoint
from .payoff import expiration_pnl, payoff_points, summarize_payoff


class StrategyBuildError(ValueError):
    """Raised when a chain cannot support the requested structure."""


@dataclass(frozen=True)
class LegSpec:
    """One leg expressed relative to the strike list the caller supplies."""

    right: str          # "C" or "P"
    action: OptionAction
    strike_index: int   # index into the sorted strike list
    quantity: int = 1


@dataclass(frozen=True)
class StrategyTemplate:
    key: str
    name: str
    description: str
    strikes_required: int
    legs: tuple[LegSpec, ...]
    thesis: str
    risk_note: str
    tags: tuple[str, ...] = field(default=())


B, S = OptionAction.BUY, OptionAction.SELL

TEMPLATES: dict[str, StrategyTemplate] = {
    "jade_lizard": StrategyTemplate(
        key="jade_lizard",
        name="Jade Lizard",
        description=(
            "Short put + short call spread (short call, long higher call). "
            "Strikes ascending: [put, short call, long call]."
        ),
        strikes_required=3,
        legs=(
            LegSpec("P", S, 0),
            LegSpec("C", S, 1),
            LegSpec("C", B, 2),
        ),
        thesis=(
            "Neutral-to-bullish premium collection. Sized so total credit "
            "exceeds the call-spread width, there is no upside risk at "
            "expiration — the entire remaining risk is below the short put."
        ),
        risk_note=(
            "Downside is substantial and undefined below the short put: the "
            "loss grows one-for-one with the underlying to zero, offset only "
            "by the credit."
        ),
        tags=("credit", "neutral", "undefined_downside"),
    ),
    "bull_put_credit_spread": StrategyTemplate(
        key="bull_put_credit_spread", name="Bull Put Credit Spread",
        description="Short higher-strike put, long lower-strike put.",
        strikes_required=2,
        legs=(LegSpec("P", B, 0), LegSpec("P", S, 1)),
        thesis="Bullish/neutral credit; profits if price stays above the short put.",
        risk_note="Defined risk equal to width minus credit.",
        tags=("credit", "bullish", "defined_risk"),
    ),
    "bear_call_credit_spread": StrategyTemplate(
        key="bear_call_credit_spread", name="Bear Call Credit Spread",
        description="Short lower-strike call, long higher-strike call.",
        strikes_required=2,
        legs=(LegSpec("C", S, 0), LegSpec("C", B, 1)),
        thesis="Bearish/neutral credit; profits if price stays below the short call.",
        risk_note="Defined risk equal to width minus credit.",
        tags=("credit", "bearish", "defined_risk"),
    ),
    "bull_call_debit_spread": StrategyTemplate(
        key="bull_call_debit_spread", name="Bull Call Debit Spread",
        description="Long lower-strike call, short higher-strike call.",
        strikes_required=2,
        legs=(LegSpec("C", B, 0), LegSpec("C", S, 1)),
        thesis="Directional bullish with capped cost and capped gain.",
        risk_note="Max loss is the debit paid.",
        tags=("debit", "bullish", "defined_risk"),
    ),
    "bear_put_debit_spread": StrategyTemplate(
        key="bear_put_debit_spread", name="Bear Put Debit Spread",
        description="Long higher-strike put, short lower-strike put.",
        strikes_required=2,
        legs=(LegSpec("P", S, 0), LegSpec("P", B, 1)),
        thesis="Directional bearish with capped cost and capped gain.",
        risk_note="Max loss is the debit paid.",
        tags=("debit", "bearish", "defined_risk"),
    ),
    "iron_condor": StrategyTemplate(
        key="iron_condor", name="Iron Condor",
        description="Bull put spread + bear call spread. Strikes ascending.",
        strikes_required=4,
        legs=(LegSpec("P", B, 0), LegSpec("P", S, 1),
              LegSpec("C", S, 2), LegSpec("C", B, 3)),
        thesis="Range-bound premium collection with defined risk on both wings.",
        risk_note="Max loss is the wider wing width minus net credit.",
        tags=("credit", "neutral", "defined_risk"),
    ),
    "short_strangle": StrategyTemplate(
        key="short_strangle", name="Short Strangle",
        description="Short put and short call at different strikes.",
        strikes_required=2,
        legs=(LegSpec("P", S, 0), LegSpec("C", S, 1)),
        thesis="Maximum premium for a range-bound view.",
        risk_note=(
            "UNDEFINED risk on BOTH sides — losses are unbounded to the "
            "upside and large to the downside."
        ),
        tags=("credit", "neutral", "undefined_risk"),
    ),
    "long_straddle": StrategyTemplate(
        key="long_straddle", name="Long Straddle",
        description="Long put and long call at the same strike.",
        strikes_required=1,
        legs=(LegSpec("P", B, 0), LegSpec("C", B, 0)),
        thesis="Long volatility; profits from a large move either way.",
        risk_note="Max loss is the combined debit if price sits at the strike.",
        tags=("debit", "volatility", "defined_risk"),
    ),
    "cash_secured_put": StrategyTemplate(
        key="cash_secured_put", name="Cash-Secured Put",
        description="Short put, collateralised with cash.",
        strikes_required=1,
        legs=(LegSpec("P", S, 0),),
        thesis="Collect premium; acquire stock at the strike if assigned.",
        risk_note="Loss grows toward the strike as the underlying falls to zero.",
        tags=("credit", "bullish", "undefined_downside"),
    ),
}


def _pick(chain: OptionChainSnapshot, strike: float, right: str) -> OptionQuote:
    for option in chain.options:
        if option.contract.right == right and abs(
            option.contract.strike - strike
        ) < 1e-6:
            return option
        
    raise StrategyBuildError(
        f"chain has no {strike:g}{right} for {chain.underlying.symbol} "
        f"{chain.month}"
    )


def _entry_price(option: OptionQuote, action: OptionAction) -> tuple[float, str]:
    """Conservative fill assumption: pay the ask, receive the bid.

    Falls back to mark, then last — each fallback is reported so a candidate
    priced off a mid is never mistaken for one priced off a real quote.
    """
    q = option.quote
    if action == OptionAction.BUY and q.ask is not None:
        return q.ask, "natural"
    if action == OptionAction.SELL and q.bid is not None:
        return q.bid, "natural"
    if q.mark is not None:
        return q.mark, "mid"
    if q.last is not None:
        return q.last, "manual"
    raise StrategyBuildError(
        f"no usable price for {option.contract.strike:g}"
        f"{option.contract.right}"
    )


def build_legs(
    chain: OptionChainSnapshot, template_key: str, strikes: list[float],
    quantity: int = 1,
) -> tuple[list[OptionLeg], list[str]]:
    """Turn a template + strike list into priced legs. Returns (legs, notes)."""
    template = TEMPLATES.get(template_key)
    if template is None:
        raise StrategyBuildError(
            f"unknown strategy {template_key!r}; "
            f"available: {', '.join(sorted(TEMPLATES))}"
        )
    ordered = sorted(strikes)
    if len(ordered) != template.strikes_required:
        raise StrategyBuildError(
            f"{template.name} needs {template.strikes_required} strike(s), "
            f"got {len(ordered)}"
        )
    legs, notes = [], []
    for spec in template.legs:
        option = _pick(chain, ordered[spec.strike_index], spec.right)
        price, basis = _entry_price(option, spec.action)
        if basis != "natural":
            notes.append(
                f"{option.contract.strike:g}{option.contract.right} priced off "
                f"{basis} — no {'ask' if spec.action == OptionAction.BUY else 'bid'} "
                "available"
            )
        legs.append(OptionLeg(
            contract=option.contract,
            action=spec.action,
            quantity=spec.quantity * quantity,
            entry_price=price,
            price_basis=basis,
        ))
    return legs, notes


def simulate(
    chain: OptionChainSnapshot, template_key: str, strikes: list[float],
    quantity: int = 1,
) -> dict:
    """Build a structure and return its full payoff profile."""
    template = TEMPLATES[template_key]
    legs, notes = build_legs(chain, template_key, strikes, quantity)
    summary = summarize_payoff(legs)
    spot = (chain.underlying_quote.mark or chain.underlying_quote.last
            or sorted(strikes)[len(strikes) // 2])
    # exact kink nodes, plus a smooth grid for charting
    nodes = payoff_points(legs, spot)
    lo = max(0.01, min(p.underlying_price for p in nodes if p.underlying_price > 0) * 0.6)
    hi = max(p.underlying_price for p in nodes) * 1.4
    grid = [lo + (hi - lo) * i / 60 for i in range(61)]
    seen = {round(p.underlying_price, 4): p for p in nodes}
    for price in grid:
        seen.setdefault(round(price, 4), PayoffPoint(
            underlying_price=round(price, 4),
            profit_loss=round(expiration_pnl(legs, price), 2)))
    points = sorted(seen.values(), key=lambda p: p.underlying_price)

    warnings = list(notes) + list(summary.warnings)
    if chain.underlying_quote.availability != "realtime":
        warnings.append(
            f"underlying quote is {chain.underlying_quote.availability}; "
            "payoff levels reflect delayed prices"
        )
    if template.key == "jade_lizard":
        call_width = sorted(strikes)[2] - sorted(strikes)[1]
        credit_per_contract = summary.net_credit / max(1, quantity) / 100.0
        if credit_per_contract >= call_width:
            warnings.append(
                f"credit {credit_per_contract:.2f} >= call width "
                f"{call_width:.2f}: no upside risk at expiration (the defining "
                "jade-lizard condition holds)"
            )
        else:
            warnings.append(
                f"credit {credit_per_contract:.2f} < call width "
                f"{call_width:.2f}: upside risk is NOT eliminated — this is a "
                "jade lizard in shape only"
            )

    return {
        "strategy": {
            "key": template.key,
            "name": template.name,
            "description": template.description,
            "thesis": template.thesis,
            "risk_note": template.risk_note,
            "tags": list(template.tags),
        },
        "symbol": chain.underlying.symbol,
        "month": chain.month,
        "quantity": quantity,
        "underlying_price": spot,
        "legs": [
            {
                "strike": leg.contract.strike,
                "right": leg.contract.right,
                "action": leg.action.value,
                "quantity": leg.quantity,
                "entry_price": leg.entry_price,
                "price_basis": leg.price_basis,
                "expiration": leg.contract.expiration.isoformat(),
                "conid": leg.contract.conid,
            }
            for leg in legs
        ],
        "summary": summary.model_dump(mode="json"),
        "payoff": [p.model_dump(mode="json") for p in points],
        "pnl_at_spot": round(expiration_pnl(legs, spot), 2),
        "warnings": warnings,
        "disclaimer": (
            "Expiration payoff only — ignores early assignment, dividends, "
            "financing, commissions, and any path before expiry. Analysis "
            "tooling, not investment advice."
        ),
    }


def list_templates() -> list[dict]:
    return [
        {
            "key": t.key, "name": t.name, "description": t.description,
            "strikes_required": t.strikes_required, "thesis": t.thesis,
            "risk_note": t.risk_note, "tags": list(t.tags),
            "legs": [
                {"right": l.right, "action": l.action.value,
                 "strike_index": l.strike_index, "quantity": l.quantity}
                for l in t.legs
            ],
        }
        for t in TEMPLATES.values()
    ]
