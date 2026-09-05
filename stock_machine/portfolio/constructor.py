"""Convert stored alpha forecasts into constrained portfolio proposals."""
from __future__ import annotations

from dataclasses import dataclass
from math import fabs

from .risk import beta as estimate_beta, correlation, realized_vol
from ..forecast_readiness import alpha_readiness
from ..market_calendar import price_freshness


@dataclass(frozen=True)
class PortfolioPolicy:
    horizon_days: int = 63
    gross_limit: float = 1.00
    net_limit: float = 0.60
    single_name_limit: float = 0.10
    sector_limit: float = 0.25
    beta_limit: float = 0.60
    min_prob_edge: float = 0.05
    min_expected_excess_pct: float = 1.0
    max_pair_correlation: float = 0.85
    vol_floor: float = 0.12


def _alpha_row(forecast: dict, horizon: int):
    alpha = (forecast or {}).get("alpha_forecast") or {}
    if alpha.get("status") != "OK":
        return None
    row = (alpha.get("horizons") or {}).get(str(horizon)) or {}
    if row.get("status") != "OK":
        return None
    return row


def build_proposal(candidates: list[dict], benchmark_rows: list[dict],
                   policy: PortfolioPolicy | None = None, *, as_of=None) -> dict:
    """Return target weights; never executes trades.

    Each candidate requires ticker, sector, forecast, and price_rows. Scores are
    expected excess return × probability edge divided by realized volatility.
    Long/short direction follows the sign of expected excess return.
    """
    policy = policy or PortfolioPolicy()
    benchmark_freshness = price_freshness(benchmark_rows[-1]["date"] if benchmark_rows else None, as_of=as_of)
    scored = []
    rejected = []
    for c in candidates:
        prices = c.get("price_rows") or []
        readiness = alpha_readiness(c.get("forecast") or {}, policy.horizon_days,
                                    latest_price_date=prices[-1]["date"] if prices else None,
                                    data_quality=c.get("data_quality"), as_of=as_of)
        if benchmark_freshness["status"] != "CURRENT":
            readiness["blockers"].append("benchmark risk inputs are missing or stale")
            readiness.update(status="BLOCKED", eligible=False)
        if not readiness["eligible"]:
            rejected.append({"ticker": c["ticker"], "readiness": readiness})
            continue
        row = _alpha_row(c.get("forecast") or {}, policy.horizon_days)
        if not row:
            continue
        expected = float(row.get("expected_excess_return_pct") or 0.0)
        prob = float(row.get("prob_outperform") or 0.5)
        edge = prob - 0.5
        if abs(edge) < policy.min_prob_edge or abs(expected) < policy.min_expected_excess_pct:
            continue
        if expected * edge <= 0:
            continue
        vol = realized_vol(c.get("price_rows") or [])
        if vol is None:
            continue
        b = estimate_beta(c.get("price_rows") or [], benchmark_rows)
        if b is None:
            continue
        score = (abs(expected) / 100.0) * abs(edge) / max(vol, policy.vol_floor)
        scored.append({
            "ticker": c["ticker"], "sector": c.get("sector") or "Unknown",
            "direction": 1.0 if expected > 0 else -1.0,
            "score": score, "expected_excess_return_pct": expected,
            "prob_outperform": prob, "realized_vol": vol, "beta": b or 0.0,
            "price_rows": c.get("price_rows") or [], "readiness": readiness,
            "forecast_id": (c.get("forecast") or {}).get("forecast_id"),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    selected = []
    sector_abs = {}
    gross = net = beta_exposure = 0.0
    score_sum = sum(x["score"] for x in scored) or 1.0

    for item in scored:
        raw = policy.gross_limit * item["score"] / score_sum
        weight = item["direction"] * min(raw, policy.single_name_limit)
        if fabs(weight) <= 1e-9:
            continue
        sector = item["sector"]
        remaining_sector = policy.sector_limit - sector_abs.get(sector, 0.0)
        remaining_gross = policy.gross_limit - gross
        cap = min(abs(weight), max(0.0, remaining_sector), max(0.0, remaining_gross))
        if cap <= 0:
            continue
        weight = item["direction"] * cap

        # Correlation gate: avoid adding a nearly duplicate exposure in the
        # same direction. Missing correlations do not block the candidate.
        blocked = False
        for prior in selected:
            if prior["weight"] * weight <= 0:
                continue
            corr = correlation(item["price_rows"], prior["price_rows"])
            if corr is None or corr >= policy.max_pair_correlation:
                blocked = True
                break
        if blocked:
            continue

        proposed_net = net + weight
        if abs(proposed_net) > policy.net_limit:
            allowed = max(0.0, policy.net_limit - abs(net))
            if allowed <= 0:
                continue
            weight = item["direction"] * min(abs(weight), allowed)

        b = item["beta"]
        proposed_beta = beta_exposure + weight * b
        if abs(proposed_beta) > policy.beta_limit and abs(b) > 1e-9:
            allowed = max(0.0, policy.beta_limit - abs(beta_exposure)) / abs(b)
            weight = item["direction"] * min(abs(weight), allowed)
        if abs(weight) <= 1e-6:
            continue

        selected.append({**item, "weight": round(weight, 6)})
        gross += abs(weight)
        net += weight
        beta_exposure += weight * b
        sector_abs[sector] = sector_abs.get(sector, 0.0) + abs(weight)

    for row in selected:
        row.pop("price_rows", None)
        row["realized_vol"] = round(row["realized_vol"], 4)
        row["beta"] = round(row["beta"], 4)
        row["score"] = round(row["score"], 6)

    return {
        "status": "OK",
        "proposal_only": True,
        "horizon_days": policy.horizon_days,
        "positions": selected,
        "rejected": rejected,
        "exposures": {
            "gross": round(gross, 6), "net": round(net, 6),
            "beta": round(beta_exposure, 6),
            "sector_abs": {k: round(v, 6) for k, v in sorted(sector_abs.items())},
        },
        "policy": policy.__dict__,
        "methodology": "stored alpha × probability edge / realized volatility, then hard portfolio constraints",
    }
