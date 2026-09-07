"""Attach persisted option-implied features to historical panel rows.

Only snapshots timestamped on or before the observation are eligible.  A
surface older than MAX_OPTION_AGE_DAYS is treated as missing rather than
forward-filled indefinitely.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import timedelta
from math import isfinite

from ..asof import utc_timestamp

from ..options.surface_features import FEATURE_NAMES

MAX_OPTION_AGE_DAYS = 10


def _load(conn, ticker: str) -> tuple[list, list[dict]]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT as_of::text, features
                 FROM option_surface_snapshots
                WHERE ticker = %s ORDER BY as_of, provider""",
            (ticker.upper(),),
        )
        rows = cur.fetchall()
    ordered = sorted(((utc_timestamp(r[0]), r[1]) for r in rows), key=lambda r: r[0])
    return [r[0] for r in ordered], [r[1] for r in ordered]


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
        target = utc_timestamp(row["as_of"])
        pos = bisect_right(dates, target) - 1
        features = _empty()
        surface_as_of = None
        if pos >= 0:
            age = target - dates[pos]
            candidate = values[pos] or {}
            meaningful = any(isinstance(candidate.get(name), (int, float))
                             and not isinstance(candidate[name], bool)
                             and isfinite(candidate[name])
                             for name in FEATURE_NAMES if not name.startswith("has_"))
            if timedelta(0) <= age <= timedelta(days=MAX_OPTION_AGE_DAYS) and meaningful:
                surface_as_of = dates[pos].isoformat()
                features.update({k: candidate[k] for k in FEATURE_NAMES if k in candidate})
                matched += 1
                per_ticker[row["ticker"]] += 1

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
        "tickers_with_matches": sum(n > 0 for n in per_ticker.values()),
        "information_cutoff": "start of as_of date UTC; explicit timestamps honored",
        "per_ticker_matches": per_ticker,
        "max_surface_age_days": MAX_OPTION_AGE_DAYS,
    }
