"""Durable immutable storage for Strategy Lab v2 results."""
from __future__ import annotations

import hashlib
import json
from datetime import date

from psycopg.types.json import Jsonb


def panel_hash(panel: list[dict]) -> str:
    """Hash only the PIT panel inputs consumed by the lab."""
    canonical = json.dumps(panel, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def save(conn, result: dict, source_panel_hash: str) -> str:
    as_of = date.today().isoformat()
    identity = json.dumps({
        "as_of": as_of,
        "panel_hash": source_panel_hash,
        "schema_version": result.get("schema_version"),
        "config": result.get("config"),
    }, sort_keys=True, separators=(",", ":"))
    run_id = "slv2_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO strategy_lab_v2_runs (run_id,as_of,panel_hash,result)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (run_id) DO NOTHING""",
            (run_id, as_of, source_panel_hash, Jsonb(result)),
        )
    conn.commit()
    return run_id


def latest(conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT run_id,as_of::text,panel_hash,result,created_at::text
               FROM strategy_lab_v2_runs
               ORDER BY created_at DESC LIMIT 1"""
        )
        row = cur.fetchone()
    if not row:
        return None
    return dict(zip(
        ["run_id", "as_of", "panel_hash", "result", "created_at"], row
    ))
