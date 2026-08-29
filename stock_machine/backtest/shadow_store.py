"""Persistence for immutable-ish P0 shadow evaluation runs."""
from __future__ import annotations

from uuid import uuid4

from psycopg.types.json import Jsonb

SCHEMA = """
CREATE TABLE IF NOT EXISTS alpha_shadow_runs (
    run_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    model_id TEXT NOT NULL,
    panel_start DATE,
    panel_end DATE,
    observation_count INT NOT NULL,
    ticker_count INT NOT NULL,
    expectations_coverage DOUBLE PRECISION NOT NULL,
    decision TEXT NOT NULL,
    result JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS alpha_shadow_runs_created_idx
    ON alpha_shadow_runs (created_at DESC);
"""


def init_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()


def save(conn, result: dict, observations: list[dict]) -> str:
    init_schema(conn)
    run_id = str(uuid4())
    dates = sorted({row["as_of"] for row in observations})
    coverage = result["coverage"]
    decision = result["promotion"]["decision"]
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO alpha_shadow_runs
               (run_id, model_id, panel_start, panel_end, observation_count,
                ticker_count, expectations_coverage, decision, result)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (run_id, result["model_id"], dates[0] if dates else None,
             dates[-1] if dates else None, coverage["observations"],
             coverage["tickers"], coverage["expectations_coverage"],
             decision, Jsonb(result)),
        )
    conn.commit()
    return run_id


def latest(conn, model_id: str | None = None) -> dict | None:
    init_schema(conn)
    sql = "SELECT run_id, created_at::text, result FROM alpha_shadow_runs"
    params = []
    if model_id:
        sql += " WHERE model_id = %s"
        params.append(model_id)
    sql += " ORDER BY created_at DESC LIMIT 1"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return None
    return {"run_id": row[0], "created_at": row[1], "result": row[2]}
