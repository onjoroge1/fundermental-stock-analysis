"""Production bootstrap helpers for the PR32 API control plane.

This module deliberately exposes no user-selectable migration target. The only
schema mutation is Alembic `upgrade head` using the app's own checked-in
migration chain and the DATABASE_URL already held by the deployed service.
"""
from __future__ import annotations

from typing import Any


def merge_research_universe(
    companies: list[dict[str, Any]], indexed_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Merge sparse research-index rows over the complete company universe.

    A partially populated index must never make unindexed companies disappear.
    This matters during gradual/API-driven refreshes, where HIMS might be the
    first indexed ticker while the existing 53-name universe remains valid.
    """
    indexed = {
        str(row.get("ticker") or "").upper(): row
        for row in indexed_rows
        if row.get("ticker")
    }
    stocks: list[dict[str, Any]] = []
    seen: set[str] = set()
    ready = 0
    for company in companies:
        ticker = str(company.get("ticker") or "").upper()
        if not ticker:
            continue
        seen.add(ticker)
        snapshot = indexed.get(ticker)
        if snapshot:
            ready += 1
            stocks.append({**company, **snapshot, "index_status": "READY"})
        else:
            stocks.append({**company, "index_status": "PENDING"})

    # Defensive: retain an indexed record even if company metadata is briefly
    # inconsistent during a refresh. It should be rare, but dropping it would
    # make the read contract less observable.
    for ticker, snapshot in indexed.items():
        if ticker not in seen:
            ready += 1
            stocks.append({**snapshot, "index_status": "READY"})

    stocks.sort(key=lambda row: str(row.get("ticker") or ""))
    return {
        "status": "OK" if ready == len(stocks) and stocks else "PARTIAL_INDEX",
        "count": len(stocks),
        "indexed_count": ready,
        "pending_count": len(stocks) - ready,
        "stocks": stocks,
    }


def migrate_to_head() -> dict[str, str]:
    """Apply the repository's fixed Alembic chain to `head` only."""
    from alembic import command
    from alembic.config import Config

    from .config import PROJECT_ROOT

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    return {"status": "OK", "migration_target": "head"}
