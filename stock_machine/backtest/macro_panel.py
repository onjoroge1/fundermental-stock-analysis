"""Attach point-in-time macro state to a regime-enriched backtest panel."""
from __future__ import annotations

from ..macro import SERIES, features_as_of, interaction_features, load_series


def enrich(conn, observations: list[dict]) -> tuple[list[dict], dict]:
    series = {sid: load_series(conn, sid) for sid in SERIES}
    enriched = []
    dates_with = {"vix": set(), "curve": set(), "credit": set()}
    for row in observations:
        copy = dict(row)
        macro = features_as_of(series, row["as_of"])
        copy["macro"] = macro
        copy["macro_interactions"] = interaction_features(copy)
        f = macro["features"]
        if f["has_vix"]:
            dates_with["vix"].add(row["as_of"])
        if f["has_curve"]:
            dates_with["curve"].add(row["as_of"])
        if f["has_credit"]:
            dates_with["credit"].add(row["as_of"])
        enriched.append(copy)
    return enriched, {
        "series_rows": {sid: len(rows) for sid, rows in series.items()},
        "dates_with_vix": len(dates_with["vix"]),
        "dates_with_curve": len(dates_with["curve"]),
        "dates_with_credit": len(dates_with["credit"]),
    }
