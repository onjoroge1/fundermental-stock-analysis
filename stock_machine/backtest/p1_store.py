"""Persistence for P1 completion research runs."""
from __future__ import annotations

from uuid import uuid4
from psycopg.types.json import Jsonb


def save(conn, result: dict, observations: list[dict]) -> str:
    run_id = str(uuid4())
    dates = sorted({row["as_of"] for row in observations})
    option_cov = float(result.get("coverage", {}).get("options", {}).get("coverage", 0.0))
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO p1_research_runs
               (run_id, panel_start, panel_end, observation_count,
                option_surface_coverage, decision, result)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (run_id, dates[0] if dates else None, dates[-1] if dates else None,
             len(observations), option_cov,
             result.get("promotion", {}).get("decision", "UNKNOWN"), Jsonb(result)),
        )
    conn.commit()
    return run_id


def latest(conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT run_id, created_at::text, result
                 FROM p1_research_runs ORDER BY created_at DESC LIMIT 1"""
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"run_id": row[0], "created_at": row[1], "result": row[2]}
