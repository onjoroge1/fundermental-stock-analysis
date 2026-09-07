"""Read-only loaders for the P0 alpha forecast input contract.

Kept separate from the normalized write path so the forecast worker can evolve
without coupling model-specific feature requirements into ingestion.
"""
from __future__ import annotations

from .asof import utc_timestamp


def fetch_consensus_history(conn, ticker: str) -> list[dict]:
    """Return every stored consensus vintage, oldest first.

    The alpha forecaster performs its own as-of lookup for each historical
    observation, so returning only the latest consensus would create hidden
    look-ahead bias.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT snapshot_date::text, period_type,
                      forecast_period_end::text,
                      revenue_mean, revenue_high, revenue_low,
                      eps_mean, eps_high, eps_low, analyst_count
                 FROM consensus_snapshots
                WHERE ticker = %s
                  AND (period_type = 'annual' OR period_basis = 'fiscal')
                ORDER BY snapshot_date, forecast_period_end""",
            (ticker,),
        )
        cols = [
            "snapshot_date", "period_type", "forecast_period_end",
            "revenue_mean", "revenue_high", "revenue_low",
            "eps_mean", "eps_high", "eps_low", "analyst_count",
        ]
        legacy = [dict(zip(cols, row)) for row in cur.fetchall()]
        for row in legacy:
            row["source"] = "legacy_unattributed"
        cur.execute("""SELECT source,observed_at,period_type,forecast_period_end::text,payload
            FROM consensus_vintages WHERE ticker=%s ORDER BY observed_at,source,forecast_period_end""", (ticker,))
        precise = [{**r[4], "source": r[0], "available_at": utc_timestamp(r[1].isoformat()).isoformat(),
                    "snapshot_date": utc_timestamp(r[1].isoformat()).date().isoformat(), "period_type": r[2],
                    "forecast_period_end": r[3]} for r in cur.fetchall()]
        return legacy + precise


def fetch_surprise_history(conn, ticker: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT event_date::text, actual_eps, estimated_eps, surprise_pct,
                      observed_at::text AS available_at
                 FROM earnings_surprise_vintages
                WHERE ticker = %s
                ORDER BY event_date, observed_at""",
            (ticker,),
        )
        cols = ["date", "actual_eps", "estimated_eps", "surprise_pct", "available_at"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
