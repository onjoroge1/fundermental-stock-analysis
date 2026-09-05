"""Actual outcome-availability guards, in addition to conservative embargoes."""
from datetime import date, timedelta


def matured_before(row: dict, horizon: str, prediction_date: str) -> bool:
    fallback_days = {"fwd_3m_pct": 91, "fwd_6m_pct": 182, "fwd_12m_pct": 365}[horizon]
    target = ((row.get("forward_target_dates") or {}).get(horizon)
              or (date.fromisoformat(row["as_of"]) + timedelta(days=fallback_days)).isoformat())
    available = (row.get("forward_available_at") or {}).get(horizon) or target
    return max(str(target), str(available)) < prediction_date
