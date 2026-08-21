"""Forward-only evaluation for promoted strategy paper cohorts."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from psycopg.types.json import Jsonb

from . import db
from .strategy_lab import DEFAULT_COST_BPS


MIN_CALENDAR_DAYS = 126
MIN_MARKS = 40
MIN_EXCESS_HIT_SHARE = 0.55
MAX_DRAWDOWN_PCT = -20.0
MIN_PRICE_COVERAGE = 1.0
SCHEMA_VERSION = "paper_incubation.v1"


def _signature(policy: str, holdings: list[dict], benchmark: list[dict]) -> str:
    payload = {
        "policy": policy,
        "holdings": sorted(row["ticker"] for row in holdings),
        "benchmark": sorted(row["ticker"] for row in benchmark),
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def _latest_cohort(conn, policy: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("""SELECT cohort_id, signature, holdings, benchmark
                       FROM sm_strategy_paper_cohorts WHERE policy=%s
                       ORDER BY created_at DESC LIMIT 1""", (policy,))
        row = cur.fetchone()
    if not row:
        return None
    return dict(zip(("cohort_id", "signature", "holdings", "benchmark"), row))


def ensure_cohorts(conn, screen: dict, screen_id: str, *,
                   cost_bps: float | None = None) -> list[dict]:
    """Create a cohort only when holdings or its frozen benchmark changes."""
    if cost_bps is None:
        cost_bps = screen.get("cost_bps_per_turnover", DEFAULT_COST_BPS)
    if cost_bps < 0:
        raise ValueError("cost_bps must be non-negative")
    benchmark = screen.get("benchmark") or []
    if not benchmark or any((row.get("price") or 0) <= 0 for row in benchmark):
        raise ValueError("screen requires a fully priced frozen benchmark")
    created = []
    for policy, result in sorted((screen.get("policies") or {}).items()):
        if result.get("status") != "PAPER_ELIGIBLE":
            continue
        holdings = result.get("candidates") or []
        if not holdings or any((row.get("price") or 0) <= 0 for row in holdings):
            raise ValueError(f"{policy} requires fully priced holdings")
        signature = _signature(policy, holdings, benchmark)
        previous = _latest_cohort(conn, policy)
        if previous and previous["signature"] == signature:
            created.append({"policy": policy,
                            "cohort_id": previous["cohort_id"],
                            "created": False})
            continue
        previous_names = ({row["ticker"] for row in previous["holdings"]}
                          if previous else set())
        selected_names = {row["ticker"] for row in holdings}
        turnover = (1.0 if not previous_names else
                    1 - len(previous_names & selected_names)
                    / max(len(previous_names), len(selected_names)))
        cost_pct = turnover * cost_bps / 100
        cohort_id = f"{screen_id}:{policy}"
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO sm_strategy_paper_cohorts
                           (cohort_id, policy, screen_id, start_date, signature,
                            turnover, cost_bps, cost_pct, holdings, benchmark)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (cohort_id) DO NOTHING""",
                        (cohort_id, policy, screen_id, screen["as_of"],
                         signature, turnover, cost_bps, cost_pct,
                         Jsonb(holdings), Jsonb(benchmark)))
        created.append({"policy": policy, "cohort_id": cohort_id,
                        "created": True, "turnover": round(turnover, 4),
                        "cost_pct": round(cost_pct, 4)})
    return created


def _marked_return(conn, members: list[dict], on: str) -> tuple[float | None,
                                                                  float,
                                                                  list[dict]]:
    details = []
    for member in members:
        rows = db.fetch_prices(conn, member["ticker"], on)
        if not rows:
            continue
        latest = rows[-1]
        age = (date.fromisoformat(on) - date.fromisoformat(latest["date"])).days
        if age > 7:
            continue
        price = latest.get("adj_close") or latest["close"]
        entry = member["price"]
        if price <= 0 or entry <= 0:
            continue
        value = (price / entry - 1) * 100
        details.append({"ticker": member["ticker"], "entry": entry,
                        "price": price, "price_date": latest["date"],
                        "return_pct": round(value, 4)})
    coverage = len(details) / len(members) if members else 0.0
    result = (sum(row["return_pct"] for row in details) / len(details)
              if details else None)
    return result, coverage, details


def mark(conn, *, on: str | None = None) -> dict:
    """Mark only the latest immutable cohort for each promoted policy."""
    on = (on or date.today().isoformat())[:10]
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT ON (policy) cohort_id, policy,
                              start_date::text, cost_pct, holdings, benchmark
                       FROM sm_strategy_paper_cohorts
                       ORDER BY policy, created_at DESC""")
        cohorts = [dict(zip(("cohort_id", "policy", "start_date", "cost_pct",
                            "holdings", "benchmark"), row))
                   for row in cur.fetchall()]
    output = []
    for cohort in cohorts:
        gross, holding_coverage, holding_details = _marked_return(
            conn, cohort["holdings"], on)
        benchmark, benchmark_coverage, benchmark_details = _marked_return(
            conn, cohort["benchmark"], on)
        coverage = min(holding_coverage, benchmark_coverage)
        price_dates = {row["price_date"] for row in
                       holding_details + benchmark_details}
        status = "OK" if (gross is not None and benchmark is not None
                           and coverage >= MIN_PRICE_COVERAGE
                           and len(price_dates) == 1) else "BLOCKED"
        evidence_date = next(iter(price_dates)) if status == "OK" else on
        net = max(-100.0, gross - cohort["cost_pct"]) if status == "OK" else None
        excess = net - benchmark if status == "OK" else None
        details = {"holdings": holding_details,
                   "benchmark": benchmark_details}
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO sm_strategy_incubation_marks
                           (cohort_id, date, status, gross_return_pct,
                            net_return_pct, benchmark_return_pct,
                            excess_return_pct, coverage, details)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (cohort_id, date) DO UPDATE SET
                             status=EXCLUDED.status,
                             gross_return_pct=EXCLUDED.gross_return_pct,
                             net_return_pct=EXCLUDED.net_return_pct,
                             benchmark_return_pct=EXCLUDED.benchmark_return_pct,
                             excess_return_pct=EXCLUDED.excess_return_pct,
                             coverage=EXCLUDED.coverage,
                             details=EXCLUDED.details""",
                        (cohort["cohort_id"], evidence_date, status, gross, net,
                         benchmark, excess, coverage, Jsonb(details)))
        output.append({"cohort_id": cohort["cohort_id"],
                       "policy": cohort["policy"], "date": evidence_date,
                       "status": status,
                       "net_return_pct": round(net, 4) if net is not None else None,
                       "benchmark_return_pct": (round(benchmark, 4)
                                                if benchmark is not None else None),
                       "excess_return_pct": (round(excess, 4)
                                             if excess is not None else None),
                       "coverage": round(coverage, 4)})
    conn.commit()
    return {"date": on, "cohorts": output}


def _max_drawdown(total_returns: list[float]) -> float | None:
    if not total_returns:
        return None
    peak, worst = 1.0, 0.0
    for value in total_returns:
        nav = 1 + value / 100
        peak = max(peak, nav)
        worst = min(worst, nav / peak - 1)
    return round(worst * 100, 3)


def evaluate_marks(start_date: str, marks: list[dict]) -> dict[str, Any]:
    valid = [row for row in marks if row.get("status") == "OK"]
    if not valid:
        return {"status": "COLLECTING", "marks": 0,
                "reason": "no complete forward marks"}
    valid.sort(key=lambda row: row["date"])
    daily_excess = []
    for prior, current in zip(valid, valid[1:]):
        strategy_base = 1 + prior["net_return_pct"] / 100
        benchmark_base = 1 + prior["benchmark_return_pct"] / 100
        if strategy_base <= 0 or benchmark_base <= 0:
            daily_excess.append(-100.0)
        else:
            strategy_return = ((1 + current["net_return_pct"] / 100)
                               / strategy_base - 1) * 100
            benchmark_return = ((1 + current["benchmark_return_pct"] / 100)
                                / benchmark_base - 1) * 100
            daily_excess.append(strategy_return - benchmark_return)
    elapsed = (date.fromisoformat(valid[-1]["date"])
               - date.fromisoformat(start_date)).days
    hit_share = (sum(value > 0 for value in daily_excess) / len(daily_excess)
                 if daily_excess else 0.0)
    drawdown = _max_drawdown([row["net_return_pct"] for row in valid])
    average_coverage = sum(row["coverage"] for row in valid) / len(valid)
    gates = {
        "minimum_calendar_days": elapsed >= MIN_CALENDAR_DAYS,
        "minimum_marks": len(valid) >= MIN_MARKS,
        "positive_cumulative_excess": valid[-1]["excess_return_pct"] > 0,
        "daily_excess_hit_rate": hit_share >= MIN_EXCESS_HIT_SHARE,
        "drawdown_within_limit": (drawdown is not None
                                  and drawdown >= MAX_DRAWDOWN_PCT),
        "price_coverage": average_coverage >= MIN_PRICE_COVERAGE,
    }
    evidence_ready = gates["minimum_calendar_days"] and gates["minimum_marks"]
    status = ("REVIEW_ELIGIBLE" if all(gates.values()) else
              "FAILED" if evidence_ready else "COLLECTING")
    return {
        "status": status, "execution_status": "PAPER_ONLY",
        "marks": len(valid), "calendar_days": elapsed,
        "net_return_pct": round(valid[-1]["net_return_pct"], 3),
        "benchmark_return_pct": round(valid[-1]["benchmark_return_pct"], 3),
        "excess_return_pct": round(valid[-1]["excess_return_pct"], 3),
        "daily_excess_hit_share": round(hit_share, 3),
        "max_drawdown_pct": drawdown,
        "average_price_coverage": round(average_coverage, 3),
        "gates": gates,
        "principle": ("REVIEW_ELIGIBLE permits human review only; it never "
                      "authorizes live capital or order submission."),
    }


def status(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT ON (policy) cohort_id, policy,
                              screen_id, start_date::text, turnover, cost_bps,
                              cost_pct, holdings, benchmark
                       FROM sm_strategy_paper_cohorts
                       ORDER BY policy, created_at DESC""")
        cohorts = [dict(zip(("cohort_id", "policy", "screen_id", "start_date",
                            "turnover", "cost_bps", "cost_pct", "holdings",
                            "benchmark"), row)) for row in cur.fetchall()]
    results = []
    for cohort in cohorts:
        with conn.cursor() as cur:
            cur.execute("""SELECT date::text, status, net_return_pct,
                                  benchmark_return_pct, excess_return_pct,
                                  coverage
                           FROM sm_strategy_incubation_marks
                           WHERE cohort_id=%s ORDER BY date""",
                        (cohort["cohort_id"],))
            cols = ["date", "status", "net_return_pct",
                    "benchmark_return_pct", "excess_return_pct", "coverage"]
            marks = [dict(zip(cols, row)) for row in cur.fetchall()]
        results.append({**cohort, "evaluation": evaluate_marks(
            cohort["start_date"], marks), "marks": marks})
    return {"schema_version": SCHEMA_VERSION,
            "status": "OK" if results else "PENDING",
            "execution_status": "PAPER_ONLY", "cohorts": results,
            "gates": {"minimum_calendar_days": MIN_CALENDAR_DAYS,
                      "minimum_marks": MIN_MARKS,
                      "daily_excess_hit_share": MIN_EXCESS_HIT_SHARE,
                      "maximum_drawdown_pct": MAX_DRAWDOWN_PCT,
                      "minimum_price_coverage": MIN_PRICE_COVERAGE}}
