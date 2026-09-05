"""Point-in-time macro/volatility/credit series support for P1.

The ingestion path uses public FRED CSV series and stores an explicit
retrieval timestamp for each observed vintage. Current revised history is
never treated as known on the original observation date. Missing historical
vintages stay missing; ALFRED release vintages would be needed to fill them.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone, timedelta
from io import StringIO

import httpx

SERIES = {
    "VIXCLS": {"label": "vix", "lag_days": 0},
    "DGS2": {"label": "ust2y", "lag_days": 1},
    "DGS10": {"label": "ust10y", "lag_days": 1},
    "BAMLH0A0HYM2": {"label": "hy_oas", "lag_days": 1},
}

MACRO_FEATURE_NAMES = [
    "vix_level",
    "vix_change_20",
    "curve_10y2y",
    "curve_change_63",
    "hy_oas",
    "hy_oas_change_20",
    "has_vix",
    "has_curve",
    "has_credit",
]


def fetch_fred_csv(series_id: str, timeout: float = 30.0) -> list[dict]:
    if series_id not in SERIES:
        raise ValueError(f"unsupported macro series: {series_id}")
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    reader = csv.DictReader(StringIO(response.text))
    out = []
    observed_at = datetime.now(timezone.utc).isoformat()
    for row in reader:
        raw = row.get(series_id)
        if raw in (None, "", "."):
            continue
        obs = date.fromisoformat(row.get("DATE") or row["observation_date"])
        out.append({
            "series_id": series_id,
            "observation_date": obs.isoformat(),
            # A current-history CSV is known now, not on its economic date.
            "available_at": observed_at,
            "value": float(raw),
            "source": "FRED",
        })
    return out


def upsert_series(conn, rows: list[dict]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO macro_series_vintages
               (series_id, observation_date, available_at, value, source)
               VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            [(r["series_id"], r["observation_date"], r["available_at"], r["value"], r["source"]) for r in rows])
        cur.executemany(
            """INSERT INTO macro_series
               (series_id, observation_date, available_at, value, source)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (series_id, observation_date) DO UPDATE SET
                 available_at = EXCLUDED.available_at,
                 value = EXCLUDED.value,
                 source = EXCLUDED.source,
                 ingested_at = now()""",
            [(r["series_id"], r["observation_date"], r["available_at"],
              r["value"], r["source"]) for r in rows],
        )
    conn.commit()
    return len(rows)


def load_series(conn, series_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT observation_date::text, available_at::text, value
                 FROM macro_series_vintages
                WHERE series_id = %s
                ORDER BY observation_date, available_at""",
            (series_id,),
        )
        return [
            {"observation_date": r[0], "available_at": r[1], "value": float(r[2])}
            for r in cur.fetchall()
        ]


def _asof(rows: list[dict], as_of: str) -> tuple[int, float] | None:
    eligible = [(i, r) for i, r in enumerate(rows) if r["available_at"] <= as_of]
    if not eligible:
        return None
    i, row = eligible[-1]
    return i, float(row["value"])


def _change(rows: list[dict], idx: int, width: int) -> float | None:
    if idx - width < 0:
        return None
    return float(rows[idx]["value"]) - float(rows[idx - width]["value"])


def features_as_of(series: dict[str, list[dict]], as_of: str) -> dict:
    # Reconstruct one value per economic date at the requested information
    # cutoff; duplicate revision rows must not lengthen rolling windows.
    series = {sid: list({r["observation_date"]: r for r in sorted(rows,
                          key=lambda r: (r["observation_date"], r["available_at"]))
                       if r["available_at"] <= as_of}.values())
              for sid, rows in series.items()}
    vix = _asof(series.get("VIXCLS", []), as_of)
    y2 = _asof(series.get("DGS2", []), as_of)
    y10 = _asof(series.get("DGS10", []), as_of)
    hy = _asof(series.get("BAMLH0A0HYM2", []), as_of)

    vix_level = vix[1] if vix else None
    vix_change = _change(series["VIXCLS"], vix[0], 20) if vix else None

    curve = (y10[1] - y2[1]) if y10 and y2 else None
    curve_change = None
    if y10 and y2 and y10[0] >= 63 and y2[0] >= 63:
        now = y10[1] - y2[1]
        old = (float(series["DGS10"][y10[0] - 63]["value"])
               - float(series["DGS2"][y2[0] - 63]["value"]))
        curve_change = now - old

    hy_level = hy[1] if hy else None
    hy_change = _change(series["BAMLH0A0HYM2"], hy[0], 20) if hy else None

    features = {
        "vix_level": vix_level,
        "vix_change_20": vix_change,
        "curve_10y2y": curve,
        "curve_change_63": curve_change,
        "hy_oas": hy_level,
        "hy_oas_change_20": hy_change,
        "has_vix": float(vix is not None),
        "has_curve": float(y10 is not None and y2 is not None),
        "has_credit": float(hy is not None),
    }
    return {"as_of": as_of, "features": features}


def interaction_features(row: dict) -> dict:
    """Turn common macro state into cross-sectional information.

    Raw macro values are identical for all names on a given date and therefore
    vanish under per-date z-scoring. Interactions let the same macro shock rank
    stocks differently according to their observable exposures.
    """
    m = (row.get("macro") or {}).get("features") or {}
    f = row.get("factors") or {}
    c = row.get("components") or {}
    r = (row.get("regime") or {}).get("features") or {}

    momentum = f.get("momentum_12m_pct") or 0.0
    valuation = c.get("valuation") or 0.0
    quality = ((c.get("profitability") or 0.0) + (c.get("financial_health") or 0.0)) / 2.0
    growth = c.get("growth") or 0.0
    realized_vol = r.get("market_vol_21") or 0.0
    sector_rel = r.get("sector_vs_spy_63") or 0.0

    return {
        "vix_x_momentum": (m.get("vix_level") or 0.0) * momentum,
        "vix_x_quality": (m.get("vix_level") or 0.0) * quality,
        "vix_change_x_sector_rel": (m.get("vix_change_20") or 0.0) * sector_rel,
        "credit_x_quality": (m.get("hy_oas") or 0.0) * quality,
        "credit_change_x_valuation": (m.get("hy_oas_change_20") or 0.0) * valuation,
        "curve_x_growth": (m.get("curve_10y2y") or 0.0) * growth,
        "curve_change_x_momentum": (m.get("curve_change_63") or 0.0) * momentum,
        "vix_x_realized_vol": (m.get("vix_level") or 0.0) * realized_vol,
    }

MACRO_INTERACTION_NAMES = [
    "vix_x_momentum", "vix_x_quality", "vix_change_x_sector_rel",
    "credit_x_quality", "credit_change_x_valuation", "curve_x_growth",
    "curve_change_x_momentum", "vix_x_realized_vol",
]
