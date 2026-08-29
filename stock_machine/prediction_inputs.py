"""Read-only loaders for the P0 alpha forecast input contract.

Kept separate from the normalized write path so the forecast worker can evolve
without coupling model-specific feature requirements into ingestion.
"""
from __future__ import annotations


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
                ORDER BY snapshot_date, forecast_period_end""",
            (ticker,),
        )
        cols = [
            "snapshot_date", "period_type", "forecast_period_end",
            "revenue_mean", "revenue_high", "revenue_low",
            "eps_mean", "eps_high", "eps_low", "analyst_count",
        ]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_surprise_history(conn, ticker: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT date::text, actual_eps, estimated_eps, surprise_pct
                 FROM earnings_surprises
                WHERE ticker = %s
                ORDER BY date""",
            (ticker,),
        )
        cols = ["date", "actual_eps", "estimated_eps", "surprise_pct"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
