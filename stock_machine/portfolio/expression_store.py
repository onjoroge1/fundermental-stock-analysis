"""Persistence for P2-B review-only trade-expression proposals."""
from __future__ import annotations

from uuid import uuid4
from psycopg.types.json import Jsonb


def save(conn, portfolio_proposal_id: str | None, result: dict) -> str:
    expression_id = str(uuid4())
    selected = result.get("selected") or {}
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO trade_expression_proposals
               (expression_id, portfolio_proposal_id, ticker, expression,
                strategy_type, status, result)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (expression_id, portfolio_proposal_id, result.get("ticker"),
             result.get("expression"), selected.get("strategy_type"),
             result.get("status", "UNKNOWN"), Jsonb(result)),
        )
    conn.commit()
    return expression_id


def latest_for_ticker(conn, ticker: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT expression_id, created_at::text, result
                 FROM trade_expression_proposals
                WHERE ticker=%s ORDER BY created_at DESC LIMIT 1""",
            (ticker.upper(),),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"expression_id": row[0], "created_at": row[1], "result": row[2]}
