"""Causal, same-period consensus comparisons shared by all model lanes."""
from datetime import date, timedelta


def consensus_revision(rows: list[dict], as_of: str, window_days: int = 30) -> dict:
    eligible = [r for r in rows if r.get("snapshot_date")
                and str(r.get("available_at") or r["snapshot_date"])[:10] < as_of[:10]
                and str(r.get("forecast_period_end") or "")[:10] > as_of[:10]
                and r.get("period_type") in ("quarter", "annual")]
    empty = {"eps_revision_pct": None, "revenue_revision_pct": None,
             "has_consensus": False, "forecast_period_end": None,
             "window_days": window_days}
    if not eligible:
        return empty
    newest = max(str(r["snapshot_date"])[:10] for r in eligible)
    current = [r for r in eligible if str(r["snapshot_date"])[:10] == newest]
    current.sort(key=lambda r: (r["period_type"] != "quarter", str(r["forecast_period_end"])))
    current = current[0]
    cutoff = (date.fromisoformat(as_of[:10]) - timedelta(days=window_days)).isoformat()
    previous = [r for r in eligible
                if r["period_type"] == current["period_type"]
                and str(r["forecast_period_end"])[:10] == str(current["forecast_period_end"])[:10]
                and r.get("source") == current.get("source")
                and str(r["snapshot_date"])[:10] <= cutoff
                and str(r["snapshot_date"])[:10] < newest]
    prior = max(previous, key=lambda r: str(r["snapshot_date"])) if previous else {}
    result = {**empty, "forecast_period_end": current["forecast_period_end"],
              "has_consensus": any(current.get(k) is not None for k in ("eps_mean", "revenue_mean")),
              "snapshot_date": newest, "prior_snapshot_date": prior.get("snapshot_date")}
    for field, name in (("eps_mean", "eps_revision_pct"), ("revenue_mean", "revenue_revision_pct")):
        old, new = prior.get(field), current.get(field)
        if old is not None and new is not None and abs(float(old)) > 1e-12:
            result[name] = (float(new) - float(old)) / abs(float(old)) * 100.0
    return result


def known_surprises(rows: list[dict], as_of: str) -> list[dict]:
    # Date-only releases cannot be assumed known at that session's close.
    eligible = sorted([r for r in rows if r.get("date") and r.get("surprise_pct") is not None
                   and str(r.get("available_at") or r["date"])[:10] < as_of[:10]],
                  key=lambda r: (str(r["date"]), str(r.get("available_at") or r["date"])))
    return list({str(r["date"]): r for r in eligible}.values())
