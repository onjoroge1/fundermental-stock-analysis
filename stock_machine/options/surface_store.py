"""Persistence helpers for option-implied feature snapshots."""
from __future__ import annotations

from uuid import uuid4

from psycopg.types.json import Jsonb


def history(conn, ticker: str, before_as_of: str | None = None, limit: int = 252) -> list[dict]:
    sql = """SELECT as_of::text, atm_iv, iv_skew_25d, term_slope,
                    expected_move_pct, put_call_oi_ratio, iv_percentile, features
               FROM option_surface_snapshots
              WHERE ticker = %s"""
    params: list[object] = [ticker.upper()]
    if before_as_of:
        sql += " AND as_of < %s"
        params.append(before_as_of)
    sql += " ORDER BY as_of DESC LIMIT %s"
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = ["as_of", "atm_iv", "iv_skew_25d", "term_slope",
                "expected_move_pct", "put_call_oi_ratio", "iv_percentile", "features"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def latest_as_of(conn, ticker: str, as_of: str, max_age_days: int = 10) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT as_of::text, provider, near_expiration::text, features
                 FROM option_surface_snapshots
                WHERE ticker = %s AND as_of <= %s
                  AND as_of >= (%s::timestamptz - (%s || ' days')::interval)
                ORDER BY as_of DESC LIMIT 1""",
            (ticker.upper(), as_of, as_of, max_age_days),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"as_of": row[0], "provider": row[1], "near_expiration": row[2],
            "features": row[3]}


def save(conn, surface: dict) -> str:
    if surface.get("status") != "OK":
        raise ValueError("only OK option surfaces may be persisted")
    f = surface["features"]
    sid = str(uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO option_surface_snapshots
               (snapshot_id, ticker, as_of, provider, near_expiration, atm_iv,
                iv_skew_25d, term_slope, expected_move_pct, put_call_oi_ratio,
                iv_percentile, features)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (ticker, as_of, provider) DO UPDATE SET
                 near_expiration=EXCLUDED.near_expiration,
                 atm_iv=EXCLUDED.atm_iv,
                 iv_skew_25d=EXCLUDED.iv_skew_25d,
                 term_slope=EXCLUDED.term_slope,
                 expected_move_pct=EXCLUDED.expected_move_pct,
                 put_call_oi_ratio=EXCLUDED.put_call_oi_ratio,
                 iv_percentile=EXCLUDED.iv_percentile,
                 features=EXCLUDED.features""",
            (sid, surface["symbol"], surface["as_of"], surface["provider"],
             surface.get("near_expiration"), f.get("atm_iv"), f.get("iv_skew_25d"),
             f.get("term_slope"), f.get("expected_move_pct"), f.get("put_call_oi_ratio"),
             f.get("iv_percentile"), Jsonb(f)),
        )
    conn.commit()
    return sid
