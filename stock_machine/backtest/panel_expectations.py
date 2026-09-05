"""Point-in-time expectations features for historical backtest observations."""
from __future__ import annotations
from ..expectations import consensus_revision, known_surprises


def _pct_change(new, old):
    if new is None or old is None:
        return None
    old = float(old)
    if abs(old) < 1e-12:
        return None
    return (float(new) - old) / abs(old) * 100.0


def expectations_as_of(consensus_rows: list[dict], surprises: list[dict],
                       as_of: str) -> dict:
    revision = consensus_revision(consensus_rows, as_of)
    past = known_surprises(surprises, as_of)
    latest = float(past[-1]["surprise_pct"]) if past else None
    trailing = (sum(float(r["surprise_pct"]) for r in past[-4:]) / min(4, len(past))
                if past else None)

    return {
        "eps_revision_pct": revision["eps_revision_pct"],
        "revenue_revision_pct": revision["revenue_revision_pct"],
        "latest_eps_surprise_pct": latest,
        "trailing_4q_eps_surprise_pct": trailing,
    }
