"""Recover actual ALFRED vintages, never backdate a current FRED download."""
import csv
import math
from datetime import date, timedelta
from io import StringIO
from urllib.parse import urlencode

import httpx

from .macro import SERIES


def fetch_vintage(series_id: str, vintage: str, lookback_days: int = 180) -> list[dict]:
    if series_id not in SERIES:
        raise ValueError(f"unsupported series: {series_id}")
    day = date.fromisoformat(vintage)
    if day >= date.today():
        raise ValueError("archive vintage must be a completed date")
    url = "https://alfred.stlouisfed.org/graph/alfredgraph.csv?" + urlencode({
        "id": series_id, "cosd": (day - timedelta(days=lookback_days)).isoformat(),
        "coed": vintage, "vintage_date": vintage,
    })
    response = httpx.get(url, timeout=60, follow_redirects=True)
    response.raise_for_status()
    return parse_vintage(response.text, series_id, vintage, url)


def parse_vintage(content: str, series_id: str, vintage: str, source: str) -> list[dict]:
    day = date.fromisoformat(vintage)
    column = f"{series_id}_{day:%Y%m%d}"
    reader = csv.DictReader(StringIO(content))
    if reader.fieldnames != ["observation_date", column]:
        raise ValueError(f"ALFRED did not confirm requested vintage {column}")
    rows = []
    for row in reader:
        observed = date.fromisoformat(row["observation_date"])
        if observed > day:
            raise ValueError("archive contains an observation after its vintage")
        if row[column] in ("", "."):
            continue
        value = float(row[column])
        if not math.isfinite(value):
            raise ValueError("nonfinite archive value")
        rows.append({
            "series_id": series_id, "observation_date": observed.isoformat(),
            # ALFRED gives a vintage day, not an intraday release time. Wait
            # until that entire day has elapsed before making it eligible.
            "available_at": (day + timedelta(days=1)).isoformat() + "T00:00:00+00:00",
            "value": value, "source": source,
        })
    return rows


def save_vintage(conn, rows: list[dict]) -> int:
    """Append archive rows only; leave the current-series cache untouched."""
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO macro_series_vintages
               (series_id, observation_date, available_at, value, source)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            [(r["series_id"], r["observation_date"], r["available_at"],
              r["value"], r["source"]) for r in rows])
        inserted = cur.rowcount
    conn.commit()
    return inserted
