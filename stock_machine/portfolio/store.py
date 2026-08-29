"""Persistence for reviewable P2 portfolio proposals."""
from __future__ import annotations

from uuid import uuid4
from psycopg.types.json import Jsonb


def save(conn, proposal: dict) -> str:
    proposal_id = str(uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO portfolio_proposals
               (proposal_id, horizon_days, gross_exposure, net_exposure,
                beta_exposure, position_count, proposal)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (proposal_id, int(proposal.get("horizon_days") or 0),
             float((proposal.get("exposures") or {}).get("gross") or 0.0),
             float((proposal.get("exposures") or {}).get("net") or 0.0),
             float((proposal.get("exposures") or {}).get("beta") or 0.0),
             len(proposal.get("positions") or []), Jsonb(proposal)),
        )
    conn.commit()
    return proposal_id


def latest(conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT proposal_id, created_at::text, proposal
                 FROM portfolio_proposals ORDER BY created_at DESC LIMIT 1"""
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"proposal_id": row[0], "created_at": row[1], "proposal": row[2]}
