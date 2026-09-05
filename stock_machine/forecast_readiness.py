"""Selected-horizon evidence and data policy for research consumers."""
from .market_calendar import price_freshness


def alpha_readiness(forecast: dict, horizon: int, *, latest_price_date=None,
                    data_quality=None, as_of=None) -> dict:
    alpha = forecast.get("alpha_forecast") or {}
    row = (alpha.get("horizons") or {}).get(str(horizon)) or {}
    blockers, reasons = [], []
    origin = alpha.get("as_of") or forecast.get("as_of")
    fresh = price_freshness(latest_price_date or origin, as_of=as_of)
    if fresh["status"] != "CURRENT" or origin != fresh["latest_market_date"]:
        blockers.append("forecast and input prices must match the latest completed session")
    quality = data_quality or forecast.get("data_quality") or {}
    if quality.get("status") not in {"READY", "CAUTION"}:
        blockers.append("required input readiness is missing or blocked")
    if alpha.get("status") != "OK" or row.get("status") != "OK":
        blockers.append("selected alpha horizon is unavailable")
    if not (row.get("validation") or {}).get("passes"):
        reasons.append("selected horizon has not passed independent validation")
    if row.get("readiness_status") != "VALIDATED":
        reasons.append("selected horizon has no validated promotion/calibration contract")
    return {"status": "BLOCKED" if blockers else "DIAGNOSTIC" if reasons else "READY",
            "eligible": not blockers and not reasons, "blockers": blockers,
            "reasons": reasons, "data_freshness": fresh, "horizon_days": horizon}
