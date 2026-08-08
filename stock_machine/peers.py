"""Sector-relative comparison built on daily metric snapshots.

Every pipeline run / daily refresh stores a compact, dated metrics row per
ticker; peer comparison reads same-sector rows — cheap, and the accumulated
snapshots double as point-in-time history for the future backtest."""
from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb

PEER_METRICS = [
    # key, label, higher_is (for display; percentile itself is direction-free)
    ("revenue_yoy_pct", "Revenue growth YoY", "higher"),
    ("gross_margin_pct", "Gross margin", "higher"),
    ("operating_margin_pct", "Operating margin", "higher"),
    ("fcf_margin_pct", "FCF margin", "higher"),
    ("roic_pct", "ROIC", "higher"),
    ("pe_ttm", "P/E (ttm)", "lower"),
    ("ev_to_revenue_ttm", "EV / revenue", "lower"),
    ("fcf_yield_pct", "FCF yield", "higher"),
    ("composite_score", "Composite score", "higher"),
]

MIN_PEERS = 4  # including the subject company


def compact_metrics(bundle: dict) -> dict:
    d = bundle["derived_metrics"]
    return {
        "revenue_yoy_pct": d["growth"]["revenue_yoy_pct"],
        "gross_margin_pct": d["profitability"]["gross_margin_pct"],
        "operating_margin_pct": d["profitability"]["operating_margin_pct"],
        "fcf_margin_pct": d["profitability"]["fcf_margin_pct"],
        "roic_pct": d["profitability"]["roic_pct"],
        "pe_ttm": d["valuation"]["pe_ttm"],
        "ev_to_revenue_ttm": d["valuation"]["ev_to_revenue_ttm"],
        "fcf_yield_pct": d["valuation"]["fcf_yield_pct"],
        "composite_score": bundle["fundamental_scores"]["composite_score"],
        "market_cap": bundle["market_snapshot"]["market_cap"],
    }


def snapshot_metrics(conn: psycopg.Connection, ticker: str, bundle: dict) -> None:
    as_of = bundle["knowledge_cutoff"][:10]
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO metric_snapshots (ticker, as_of, metrics)
               VALUES (%s, %s, %s)
               ON CONFLICT (ticker, as_of) DO UPDATE
               SET metrics = EXCLUDED.metrics""",
            (ticker, as_of, Jsonb(compact_metrics(bundle))))
    conn.commit()


def percentile_rank(values: list[float], own: float) -> float:
    """Percent of peer observations at or below own value (midpoint ties)."""
    below = sum(1 for v in values if v < own)
    ties = sum(1 for v in values if v == own)
    return round(100 * (below + 0.5 * ties) / len(values), 1)


def get_peer_comparison(conn: psycopg.Connection, ticker: str,
                        as_of: str) -> dict:
    """Sector-relative percentiles from the latest snapshot per peer on or
    before as_of. Refuses (available=False) below MIN_PEERS — a percentile
    among two companies is noise dressed as precision."""
    as_of_date = as_of[:10]
    with conn.cursor() as cur:
        cur.execute("SELECT sector FROM companies WHERE ticker = %s", (ticker,))
        row = cur.fetchone()
        sector = row[0] if row else None
        if not sector or sector in ("Unclassified", "Other"):
            return {"available": False, "sector": sector,
                    "reason": "no usable sector classification"}
        cur.execute(
            """SELECT DISTINCT ON (m.ticker) m.ticker, m.as_of::text, m.metrics
               FROM metric_snapshots m
               JOIN companies c ON c.ticker = m.ticker
               WHERE c.sector = %s AND m.as_of <= %s
               ORDER BY m.ticker, m.as_of DESC""",
            (sector, as_of_date))
        rows = cur.fetchall()

    peers = {t: m for t, _, m in rows}
    if ticker not in peers or len(peers) < MIN_PEERS:
        return {"available": False, "sector": sector,
                "peer_count": len(peers),
                "reason": f"need >= {MIN_PEERS} same-sector companies with "
                          f"metric snapshots (have {len(peers)})"}

    own = peers[ticker]
    comparison = []
    for key, label, higher_is in PEER_METRICS:
        values = [m.get(key) for m in peers.values() if m.get(key) is not None]
        own_v = own.get(key)
        if own_v is None or len(values) < MIN_PEERS:
            comparison.append({"metric": key, "label": label, "value": own_v,
                               "percentile": None, "sector_median": None,
                               "n": len(values), "higher_is": higher_is})
            continue
        values_sorted = sorted(values)
        mid = len(values_sorted) // 2
        median = (values_sorted[mid] if len(values_sorted) % 2
                  else (values_sorted[mid - 1] + values_sorted[mid]) / 2)
        comparison.append({
            "metric": key, "label": label, "value": own_v,
            "percentile": percentile_rank(values, own_v),
            "sector_median": round(median, 2),
            "n": len(values), "higher_is": higher_is,
        })
    return {
        "available": True,
        "sector": sector,
        "peer_count": len(peers),
        "peers": sorted(peers),
        "methodology": ("latest metric snapshot per same-sector company on or "
                        "before the bundle knowledge cutoff; percentile = "
                        "share of sector at or below the company's value"),
        "comparison": comparison,
    }
