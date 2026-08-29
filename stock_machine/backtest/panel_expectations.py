"""Point-in-time expectations features for historical backtest observations."""
from __future__ import annotations


def _pct_change(new, old):
    if new is None or old is None:
        return None
    old = float(old)
    if abs(old) < 1e-12:
        return None
    return (float(new) - old) / abs(old) * 100.0


def expectations_as_of(consensus_rows: list[dict], surprises: list[dict],
                       as_of: str) -> dict:
    vintages: dict[str, list[dict]] = {}
    for row in consensus_rows:
        snap = str(row.get("snapshot_date") or "")[:10]
        if snap and snap <= as_of:
            vintages.setdefault(snap, []).append(row)

    ordered = sorted(vintages)
    eps_revision = revenue_revision = None
    if ordered:
        current_day = ordered[-1]
        previous_day = ordered[-2] if len(ordered) > 1 else None

        def nearest(rows):
            q = [r for r in rows if r.get("period_type") == "quarter"]
            pool = q or rows
            return sorted(pool,
                          key=lambda r: str(r.get("forecast_period_end") or "9999-12-31"))[0]

        current = nearest(vintages[current_day])
        previous = nearest(vintages[previous_day]) if previous_day else None
        if previous:
            eps_revision = _pct_change(current.get("eps_mean"), previous.get("eps_mean"))
            revenue_revision = _pct_change(current.get("revenue_mean"), previous.get("revenue_mean"))

    past = [r for r in surprises
            if str(r.get("date") or "")[:10] <= as_of
            and r.get("surprise_pct") is not None]
    latest = float(past[-1]["surprise_pct"]) if past else None
    trailing = (sum(float(r["surprise_pct"]) for r in past[-4:]) / min(4, len(past))
                if past else None)

    return {
        "eps_revision_pct": eps_revision,
        "revenue_revision_pct": revenue_revision,
        "latest_eps_surprise_pct": latest,
        "trailing_4q_eps_surprise_pct": trailing,
    }
