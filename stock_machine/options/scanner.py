"""Strike-combination scanner: search a chain for the structures that best
satisfy an explicitly stated objective.

"Best" is never assumed. Every scan states its objective, its hard filters,
and its ranking metric, and returns the losers' reasons alongside the
winners so a thin result set is explainable rather than mysterious.

Two ranking modes:
  return_on_risk  — pure structure geometry (max profit / max loss). Needs no
                    forecast and carries no prediction risk.
  expected_value  — payoff integrated over THIS system's own price forecast.
                    Honest but uncalibrated: the forecast's probabilities have
                    not yet been validated, so EV inherits that uncertainty.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from ..market_data.models import OptionChainSnapshot
from .payoff import expiration_pnl, summarize_payoff
from .simulator import TEMPLATES, StrategyBuildError, build_legs

# Percentile buckets from the prediction lab's fan, with the probability mass
# each represents. Coarse by construction — a 5-point discretisation of a
# distribution we do not yet trust to more precision.
_FORECAST_BUCKETS = (("p10", 0.10), ("p25", 0.20), ("p50", 0.40),
                     ("p75", 0.20), ("p90", 0.10))

MAX_COMBINATIONS = 4000


@dataclass
class ScanPolicy:
    objective: str = "return_on_risk"      # or "expected_value"
    require_no_upside_risk: bool = False   # jade-lizard defining condition
    require_defined_risk: bool = False
    min_credit: float | None = None        # dollars, per structure
    max_collateral: float | None = None    # dollars
    min_return_on_risk: float | None = None
    top_n: int = 10


def _forecast_prices(forecast: dict | None, horizon: str = "1m"
                     ) -> list[tuple[float, float]]:
    """(price, probability) pairs from a cached prediction-lab payload."""
    if not forecast or forecast.get("status") != "OK":
        return []
    model = forecast.get("primary_model")
    horizons = ((forecast.get("models") or {}).get(model) or {}).get("horizons") or {}
    row = horizons.get(horizon)
    if not row:
        return []
    out = []
    for key, mass in _FORECAST_BUCKETS:
        price = row.get(key)
        if price is not None:
            out.append((float(price), mass))
    total = sum(m for _, m in out)
    return [(p, m / total) for p, m in out] if total else []


def _expected_pnl(legs, distribution: list[tuple[float, float]]) -> float | None:
    if not distribution:
        return None
    return round(sum(expiration_pnl(legs, price) * mass
                     for price, mass in distribution), 2)


def scan(
    chain: OptionChainSnapshot, template_key: str,
    policy: ScanPolicy | None = None, forecast: dict | None = None,
    horizon: str = "1m",
) -> dict:
    """Enumerate every strike combination the chain supports and rank them."""
    policy = policy or ScanPolicy()
    template = TEMPLATES.get(template_key)
    if template is None:
        raise StrategyBuildError(f"unknown strategy {template_key!r}")

    strikes = sorted({o.contract.strike for o in chain.options})
    if len(strikes) < template.strikes_required:
        raise StrategyBuildError(
            f"chain has {len(strikes)} strikes; {template.name} needs "
            f"{template.strikes_required}")

    combos = list(combinations(strikes, template.strikes_required))
    truncated = len(combos) > MAX_COMBINATIONS
    if truncated:
        combos = combos[:MAX_COMBINATIONS]

    distribution = _forecast_prices(forecast, horizon)
    if policy.objective == "expected_value" and not distribution:
        raise StrategyBuildError(
            "expected_value ranking needs a cached prediction-lab forecast "
            f"for {chain.underlying.symbol}; none is available")

    spot = (chain.underlying_quote.mark or chain.underlying_quote.last
            or strikes[len(strikes) // 2])
    rows, rejected = [], {}

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for combo in combos:
        try:
            legs, notes = build_legs(chain, template_key, list(combo))
        except StrategyBuildError:
            reject("incomplete quotes for one or more legs")
            continue
        summary = summarize_payoff(legs)

        if policy.require_defined_risk and not summary.defined_risk:
            reject("undefined risk")
            continue
        if policy.min_credit is not None and summary.net_credit < policy.min_credit:
            reject(f"credit below {policy.min_credit:g}")
            continue
        if (policy.max_collateral is not None
                and (summary.collateral_estimate or 0) > policy.max_collateral):
            reject(f"collateral above {policy.max_collateral:g}")
            continue

        # "no upside risk" = P&L never negative above the highest strike
        upside_safe = expiration_pnl(legs, max(combo) * 3) >= 0
        if policy.require_no_upside_risk and not upside_safe:
            reject("upside risk not eliminated")
            continue

        ror = summary.return_on_risk
        if (policy.min_return_on_risk is not None
                and (ror is None or ror < policy.min_return_on_risk)):
            reject(f"return on risk below {policy.min_return_on_risk:g}")
            continue

        expected = _expected_pnl(legs, distribution)
        rows.append({
            "strikes": list(combo),
            "net_credit": summary.net_credit,
            "max_profit": summary.max_profit,
            "max_loss": summary.max_loss,
            "return_on_risk": ror,
            "collateral_estimate": summary.collateral_estimate,
            "breakevens": summary.breakevens,
            "defined_risk": summary.defined_risk,
            "no_upside_risk": upside_safe,
            "expected_pnl": expected,
            "pnl_at_spot": round(expiration_pnl(legs, spot), 2),
            "pricing_notes": notes,
        })

    if policy.objective == "expected_value":
        rows.sort(key=lambda r: (r["expected_pnl"] is None, -(r["expected_pnl"] or 0)))
        objective_text = (
            f"expected P&L over the {horizon} forecast distribution "
            "(UNCALIBRATED — the forecast's probabilities are not yet validated)")
    else:
        rows.sort(key=lambda r: (r["return_on_risk"] is None,
                                 -(r["return_on_risk"] or 0)))
        objective_text = "return on risk (max profit / max loss), forecast-free"

    warnings = list(chain.warnings)
    if truncated:
        warnings.append(
            f"strike ladder produced more than {MAX_COMBINATIONS} combinations; "
            "only the first were scanned — narrow the strike window")
    if not rows:
        warnings.append("no combination passed the filters")

    return {
        "symbol": chain.underlying.symbol,
        "month": chain.month,
        "strategy": {"key": template.key, "name": template.name,
                     "risk_note": template.risk_note},
        "underlying_price": spot,
        "objective": objective_text,
        "filters": {
            "require_no_upside_risk": policy.require_no_upside_risk,
            "require_defined_risk": policy.require_defined_risk,
            "min_credit": policy.min_credit,
            "max_collateral": policy.max_collateral,
            "min_return_on_risk": policy.min_return_on_risk,
        },
        "combinations_evaluated": len(combos),
        "candidates_passing": len(rows),
        "rejected_reasons": rejected,
        "results": rows[: policy.top_n],
        "warnings": warnings,
        "disclaimer": (
            "Ranking a structure is not a recommendation to trade it. "
            "Expiration payoff only; ignores early assignment, dividends, "
            "financing, commissions, and path. Not investment advice."
        ),
    }
