"""Base-rate engine: before forecasting a company, ask what happened to
historically comparable setups in our own point-in-time panel.

A setup = the joint bucket (growth tercile × valuation tercile × ROIC
tercile) computed across the latest backtest run's observations. The answer
is the distribution of forward 12-month EXCESS returns (vs the date's
universe mean) over all historical setups in the same bucket.

Honesty: the panel is survivorship-biased (today's 43 names), so base rates
are optimistic in level; the cross-sectional comparison is the usable part.
Below MIN_ANALOGS the engine abstains."""
from __future__ import annotations

from collections import defaultdict

MIN_ANALOGS = 30
BUCKET_FACTORS = ["revenue_yoy_pct", "earnings_yield_pct", "roic_pct"]


def _terciles(values: list[float]) -> tuple[float, float]:
    s = sorted(values)
    return s[len(s) // 3], s[2 * len(s) // 3]


def _bucket(v: float | None, cuts: tuple[float, float]) -> str | None:
    if v is None:
        return None
    return "low" if v <= cuts[0] else ("high" if v > cuts[1] else "mid")


_panel_cache: dict = {"at": 0.0, "panel": None}


def load_panel_cached(conn, ttl_s: float = 600) -> list[dict]:
    import time
    if (_panel_cache["panel"] is None
            or time.monotonic() - _panel_cache["at"] > ttl_s):
        _panel_cache["panel"] = load_panel(conn)
        _panel_cache["at"] = time.monotonic()
    return _panel_cache["panel"]


def load_panel(conn) -> list[dict]:
    """Observations from the most recent backtest run with 12m forward."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT o.as_of::text, o.ticker, o.factors, o.forward,
                   o.composite, o.components
            FROM backtest_observations o
            WHERE o.run_id = (SELECT run_id FROM sm_backtest_runs
                              ORDER BY created_at DESC LIMIT 1)""")
        rows = [{"as_of": a, "ticker": t, "factors": f, "forward": fw,
                 "composite": comp, "components": comps}
                for a, t, f, fw, comp, comps in cur.fetchall()]
    return [r for r in rows if r["forward"].get("fwd_12m_pct") is not None]


def compute_base_rates(panel: list[dict], subject: dict) -> dict:
    """subject: {factor: value} for BUCKET_FACTORS. Pure function."""
    if not panel:
        return {"status": "NO_PANEL",
                "reason": "no backtest run available — run "
                          "`python -m stock_machine backtest` first"}
    # per-date universe mean → excess returns
    by_date: dict[str, list[float]] = defaultdict(list)
    for r in panel:
        by_date[r["as_of"]].append(r["forward"]["fwd_12m_pct"])
    date_mean = {d: sum(v) / len(v) for d, v in by_date.items()}

    cuts = {}
    for f in BUCKET_FACTORS:
        vals = [r["factors"].get(f) for r in panel
                if r["factors"].get(f) is not None]
        if len(vals) < MIN_ANALOGS:
            return {"status": "INSUFFICIENT_PANEL", "reason": f"factor {f}"}
        cuts[f] = _terciles(vals)

    subject_buckets = {f: _bucket(subject.get(f), cuts[f])
                       for f in BUCKET_FACTORS}
    if None in subject_buckets.values():
        missing = [f for f, b in subject_buckets.items() if b is None]
        return {"status": "INSUFFICIENT_DATA",
                "reason": f"subject missing factors: {', '.join(missing)}"}

    analogs = []
    for r in panel:
        if all(_bucket(r["factors"].get(f), cuts[f]) == subject_buckets[f]
               for f in BUCKET_FACTORS):
            analogs.append(r["forward"]["fwd_12m_pct"]
                           - date_mean[r["as_of"]])
    if len(analogs) < MIN_ANALOGS:
        return {"status": "INSUFFICIENT_ANALOGS",
                "subject_buckets": subject_buckets,
                "n_analogs": len(analogs),
                "reason": f"only {len(analogs)} historical setups match "
                          f"(need {MIN_ANALOGS}) — no base rate issued"}

    analogs.sort()
    n = len(analogs)
    return {
        "status": "OK",
        "subject_buckets": subject_buckets,
        "n_analogs": n,
        "outperform_share": round(sum(1 for a in analogs if a > 0) / n, 3),
        "median_excess_12m_pct": round(analogs[n // 2], 2),
        "p10_excess_12m_pct": round(analogs[int(n * 0.10)], 2),
        "p90_excess_12m_pct": round(analogs[int(n * 0.90)], 2),
        "methodology": "12m return minus same-date universe mean, all "
                       "panel setups sharing the subject's growth/valuation/"
                       "ROIC terciles; survivorship-biased panel — relative "
                       "reads only",
    }
