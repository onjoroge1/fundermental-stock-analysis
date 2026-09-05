"""Analyst consensus + earnings-surprise ingestion from FMP.

Calibrated to the free/basic plan (probed 2026-07-24):
- /stable/earnings (limit<=5): per-event actual vs estimated EPS/revenue.
  Past events feed the surprise history (accumulated append-only as the
  window slides); the next event feeds a current-quarter estimate.
- /stable/analyst-estimates period=annual (limit<=5): forward annual
  consensus with high/low/mean. period=quarter is premium on this plan.

Vintage discipline: FMP serves CURRENT consensus. Every fetch is stored with
its snapshot date; daily refreshes accumulate our own point-in-time history.
Surprises are vendor-recorded, not independently verified.

Without FMP_API_KEY everything degrades to (no data + a data-quality event) —
never a fabricated value."""
from __future__ import annotations

import time

import httpx

from ..config import FMP_API_KEY
from ..provenance import save_raw

BASE = "https://financialmodelingprep.com"
PLAN_LIMIT = 5           # free-tier cap on `limit`
UPGRADED_LIMIT = 40      # tried first; falls back if the plan rejects it

MISSING_CONSENSUS_EVENT = {
    "event": "MISSING_DATASET",
    "dataset": "consensus_estimates",
    "detail": "No estimates provider configured (FMP_API_KEY unset). "
              "Expectations analysis and surprise scoring are prohibited.",
}


def _get(path: str, params: dict) -> tuple[list | None, dict | None]:
    """Fetch one FMP endpoint. Returns (payload, error_event)."""
    time.sleep(0.3)  # free tier: 250 calls/day — be polite, never burst
    try:
        resp = httpx.get(f"{BASE}{path}",
                         params={**params, "apikey": FMP_API_KEY}, timeout=60)
    except httpx.HTTPError as e:
        return None, {"event": "PROVIDER_ERROR", "dataset": path,
                      "detail": f"FMP request failed: {type(e).__name__}: {e}"}
    text = resp.text
    if resp.status_code in (401, 402, 403) or text.startswith("Premium"):
        return None, {
            "event": "PROVIDER_PLAN_LIMIT", "dataset": path,
            "detail": f"FMP plan limit for {path}: {text[:180]}"}
    if resp.status_code != 200:
        return None, {"event": "PROVIDER_ERROR", "dataset": path,
                      "detail": f"FMP {resp.status_code}: {text[:180]}"}
    try:
        payload = resp.json()
    except ValueError:
        return None, {"event": "PROVIDER_ERROR", "dataset": path,
                      "detail": f"FMP non-JSON response: {text[:180]}"}
    if isinstance(payload, dict):
        return None, {"event": "PROVIDER_ERROR", "dataset": path,
                      "detail": str(payload)[:180]}
    return payload, None


# plan capabilities learned once per process — a fallback that repeated for
# every ticker would double the daily call count against the free-tier cap
_plan = {"limit_cap": None, "quarter_estimates": None}


def _get_adaptive(path: str, params: dict) -> tuple[list | None, dict | None]:
    """Try the upgraded-plan limit first so a subscription upgrade activates
    with zero code changes; learn the cap once and reuse it."""
    limit = _plan["limit_cap"] or UPGRADED_LIMIT
    payload, err = _get(path, {**params, "limit": limit})
    if err and "limit" in err.get("detail", "").lower():
        _plan["limit_cap"] = PLAN_LIMIT
        payload, err = _get(path, {**params, "limit": PLAN_LIMIT})
    return payload, err


def _pick(row: dict, *names):
    for n in names:
        if row.get(n) is not None:
            return row[n]
    return None


def fetch_estimates(ticker: str) -> dict:
    """Returns {snapshots, surprises, events}."""
    if not FMP_API_KEY:
        return {"snapshots": [], "surprises": [],
                "events": [MISSING_CONSENSUS_EVENT]}

    symbol = ticker.upper()
    events: list[dict] = []
    snapshots: list[dict] = []
    surprises: list[dict] = []

    # ---- forward consensus: quarterly is premium-gated, so it is attempted
    # and falls back silently to annual-only on the free plan ----
    for period in ("annual", "quarter"):
        if period == "quarter" and _plan["quarter_estimates"] is False:
            continue  # premium-gated on this plan; learned once
        payload, err = _get_adaptive(
            "/stable/analyst-estimates", {"symbol": symbol, "period": period})
        if err:
            if period == "quarter" and "period" in err.get("detail", ""):
                _plan["quarter_estimates"] = False
            elif period == "annual":
                events.append(err)
            continue
        if period == "quarter":
            _plan["quarter_estimates"] = True
        if not payload:
            continue
        save_raw("estimates", [symbol, f"fmp_analyst_estimates_{period}"],
                 payload, f"{BASE}/stable/analyst-estimates?period={period}")
        for row in payload:
            snapshots.append({
                "period_type": period,
                "forecast_period_end": row.get("date"),
                "revenue_mean": _pick(row, "revenueAvg"),
                "revenue_high": _pick(row, "revenueHigh"),
                "revenue_low": _pick(row, "revenueLow"),
                "eps_mean": _pick(row, "epsAvg"),
                "eps_high": _pick(row, "epsHigh"),
                "eps_low": _pick(row, "epsLow"),
                "analyst_count": _pick(row, "numAnalystsEps",
                                       "numAnalystsRevenue"),
            })

    # ---- per-event actual vs estimate (past → surprises, next → estimate) --
    payload, err = _get_adaptive("/stable/earnings", {"symbol": symbol})
    if err:
        events.append(err)
    elif payload:
        save_raw("estimates", [symbol, "fmp_earnings_events"], payload,
                 f"{BASE}/stable/earnings")
        for row in payload:
            actual = _pick(row, "epsActual")
            est = _pick(row, "epsEstimated")
            if actual is not None and est is not None:
                surprises.append({
                    "date": row.get("date"),
                    "actual_eps": actual,
                    "estimated_eps": est,
                    "surprise_pct": (round((actual - est) / abs(est) * 100, 2)
                                     if est else None),
                })
            # The earnings endpoint's date is an announcement date, not a
            # fiscal period end. Do not mix it with analyst-estimate vintages.

    if snapshots or surprises:
        events.append({
            "event": "DATASET_LIMITATION", "dataset": "consensus_estimates",
            "detail": "FMP consensus is current-vintage; point-in-time "
                      "history accumulates from daily snapshots. Surprise "
                      "history is vendor-recorded and window-limited "
                      f"(last {PLAN_LIMIT} events per pull, accumulated "
                      "append-only across refreshes).",
        })
    return {"snapshots": snapshots, "surprises": surprises, "events": events}
