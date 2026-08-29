"""Attach persisted option-implied features to historical panel rows.

Only snapshots timestamped on or before the observation are eligible.  A
surface older than MAX_OPTION_AGE_DAYS is treated as missing rather than
forward-filled indefinitely.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timezone

from ..options.surface_features import FEATURE_NAMES

MAX_OPTION_AGE_DAYS = 10


def _load(conn, ticker: str) -> tuple[list[str], list[dict]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT as_of::text, features
                 FROM option_surface_snapshots
                WHERE ticker = %s ORDER BY as_of""",
            (ticker.upper(),),
        )
        rows = cur.fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def _empty() -> dict:
    return {name: (0.0 if name.startswith("has_") else None) for name in FEATURE_NAMES}


def enrich(conn, observations: list[dict]) -> tuple[list[dict], dict]:
    tickers = sorted({r["ticker"] for r in observations})
    histories = {t: _load(conn, t) for t in tickers}
    enriched = []
    matched = 0
    per_ticker = {t: 0 for t in tickers}

    for row in observations:
        dates, values = histories[row["ticker"]]
        pos = bisect_right(dates, row["as_of"] + "T23:59:59") - 1
        features = _empty()
        surface_as_of = None
        if pos >= 0:
            surface_as_of = dates[pos]
            try:
                observed = datetime.fromisoformat(surface_as_of.replace("Z", "+00:00"))
                target = datetime.fromisoformat(row["as_of"] + "T23:59:59+00:00")
                age = (target - observed.astimezone(timezone.utc)).days
            except ValueError:
                age = MAX_OPTION_AGE_DAYS + 1
            if 0 <= age <= MAX_OPTION_AGE_DAYS:
                features = dict(values[pos] or {})
                matched += 1
                per_ticker[row["ticker"]] += 1
            else:
                surface_as_of = None

        copy = dict(row)
        copy["options_implied"] = {
            "as_of": surface_as_of,
            "features": features,
            "available": surface_as_of is not None,
        }
        enriched.append(copy)

    total = len(observations)
    return enriched, {
        "observations": total,
        "matched_option_surfaces": matched,
        "coverage": round(matched / total, 4) if total else 0.0,
        "tickers_with_history": sum(bool(histories[t][0]) for t in tickers),
        "per_ticker_matches": per_ticker,
        "max_surface_age_days": MAX_OPTION_AGE_DAYS,
    }
