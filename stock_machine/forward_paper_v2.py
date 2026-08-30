"""Forward Paper Incubation v2.

Freezes current baskets only from Strategy Lab v2 policies that cleared the
untouched evaluation gate, then accumulates complete same-market-date forward
marks. Cohorts are immutable and never rebalance themselves.
"""
from __future__ import annotations

import bisect
import hashlib
import json
from datetime import date
from typing import Any

from . import db
from .backtest.engine import TickerData
from .strategy_lab_v2 import STRATEGIES, TAIL_FRACTION, MAX_SECTOR_SHARE_PER_LEG, _select, score_policy_rows

SCHEMA_VERSION = "forward_paper.v2"
MIN_CALENDAR_DAYS = 126
MIN_MARKS = 40
MIN_POSITIVE_EXCESS_SHARE = 0.55
MAX_DRAWDOWN_PCT = -20.0
ENTRY_COST_BPS = 15.0


def _latest_price_row(rows: list[dict], as_of: str) -> dict | None:
    dates = [r["date"] for r in rows]
    i = bisect.bisect_right(dates, as_of[:10]) - 1
    return rows[i] if i >= 0 else None


def current_cross_section(conn, as_of: str | None = None) -> tuple[list[dict], dict[str, dict]]:
    """Build today's PIT signal cross-section and exact latest price metadata."""
    as_of = (as_of or date.today().isoformat())[:10]
    observations: list[dict] = []
    prices: dict[str, dict] = {}
    for company in db.list_companies(conn):
        ticker = company["ticker"]
        td = TickerData(conn, ticker)
        obs = td.observe(as_of)
        if not obs:
            continue
        price_row = _latest_price_row(td.prices, as_of)
        if not price_row:
            continue
        adj = price_row.get("adj_close") or price_row.get("close")
        if not adj:
            continue
        observations.append(obs)
        prices[ticker] = {
            "market_date": price_row["date"],
            "adj_close": float(adj),
            "close": float(price_row.get("close") or adj),
        }
    return observations, prices


def _common_market_date(tickers: list[str], prices: dict[str, dict]) -> str | None:
    dates = {prices[t]["market_date"] for t in tickers if t in prices}
    return next(iter(dates)) if len(dates) == 1 and len(tickers) == len([t for t in tickers if t in prices]) else None


def build_contract(lab_row: dict, policy_name: str, mode: str,
                   observations: list[dict], prices: dict[str, dict],
                   *, cost_bps: float = ENTRY_COST_BPS) -> dict:
    """Freeze one current policy basket. Raises rather than creating partial cohorts."""
    result = lab_row["result"]
    mode_result = (result.get("modes") or {}).get(mode) or {}
    policy_result = (mode_result.get("strategies") or {}).get(policy_name) or {}
    promotion = policy_result.get("promotion") or {}
    if promotion.get("status") != "ELIGIBLE_FOR_FORWARD_PAPER_REVIEW":
        raise ValueError(f"{mode}/{policy_name} is not eligible for forward paper review")
    definition = STRATEGIES.get(policy_name)
    if not definition:
        raise ValueError(f"unknown Strategy Lab v2 policy {policy_name}")
    scored = score_policy_rows(observations, definition)
    if not scored:
        raise ValueError("current cross-section lacks sufficient policy signal coverage")

    by_ticker = {row["ticker"]: row for row in observations}
    ranking = sorted(scored["scores"], key=lambda t: (-scored["scores"][t], t))
    count = max(2, __import__("math").ceil(len(observations) * TAIL_FRACTION))
    longs = _select(ranking, by_ticker, count, MAX_SECTOR_SHARE_PER_LEG)
    shorts: list[str] = []
    if mode == "long_short":
        short_rank = [t for t in reversed(ranking) if t not in set(longs)]
        shorts = _select(short_rank, by_ticker, count, MAX_SECTOR_SHARE_PER_LEG)
    elif mode != "long_only":
        raise ValueError(f"unsupported forward-paper mode {mode}")

    if len(longs) < 2 or (mode == "long_short" and len(shorts) < 2):
        raise ValueError("current policy selection has insufficient holdings")
    eligible_universe = sorted(by_ticker)
    required = sorted(set(longs + shorts + (eligible_universe if mode == "long_only" else [])))
    common_date = _common_market_date(required, prices)
    if not common_date:
        raise ValueError("entry prices do not share one exact market date; cohort creation aborted")

    entries = {t: prices[t]["adj_close"] for t in required}
    return {
        "schema_version": SCHEMA_VERSION,
        "lab_run_id": lab_row["run_id"],
        "lab_panel_hash": lab_row["panel_hash"],
        "policy_name": policy_name,
        "mode": mode,
        "entry_market_date": common_date,
        "longs": longs,
        "shorts": shorts,
        "frozen_eligible_universe": eligible_universe,
        "entry_adjusted_close": entries,
        "signal_scores": {t: round(scored["scores"][t], 6) for t in sorted(set(longs + shorts))},
        "signal_percentiles": {t: scored["signal_percentiles"][t] for t in sorted(set(longs + shorts))},
        "cost_bps": float(cost_bps),
        "control": "frozen_equal_weight_universe" if mode == "long_only" else "zero_return_market_neutral_control",
        "creation_rule": "explicit sync only; immutable cohort; never auto-rebalances",
    }


def contract_hash(contract: dict) -> str:
    canonical = json.dumps(contract, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _identity_hash(contract: dict) -> str:
    """Ignore entry date/prices when deciding whether sync would reset same basket."""
    identity = {
        "lab_run_id": contract["lab_run_id"],
        "policy_name": contract["policy_name"],
        "mode": contract["mode"],
        "longs": contract["longs"],
        "shorts": contract["shorts"],
        "frozen_eligible_universe": contract["frozen_eligible_universe"],
        "cost_bps": contract["cost_bps"],
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sync_cohort(conn, contract: dict) -> dict:
    """Create once; reuse an existing same-policy/same-basket cohort to preserve age."""
    ident = _identity_hash(contract)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT cohort_id,contract FROM forward_paper_v2_cohorts
               WHERE policy_name=%s AND mode=%s ORDER BY created_at DESC""",
            (contract["policy_name"], contract["mode"]),
        )
        for cohort_id, existing in cur.fetchall():
            if (existing or {}).get("identity_hash") == ident:
                return {"action": "reused", "cohort_id": cohort_id, "contract": existing}

    stored = {**contract, "identity_hash": ident}
    chash = contract_hash(stored)
    cohort_id = "fpv2_" + chash[:20]
    from psycopg.types.json import Jsonb
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO forward_paper_v2_cohorts
               (cohort_id,lab_run_id,policy_name,mode,entry_market_date,contract_hash,contract)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (cohort_id) DO NOTHING""",
            (cohort_id, stored["lab_run_id"], stored["policy_name"], stored["mode"],
             stored["entry_market_date"], chash, Jsonb(stored)),
        )
    conn.commit()
    return {"action": "created", "cohort_id": cohort_id, "contract": stored}


def list_cohorts(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT cohort_id,lab_run_id,policy_name,mode,entry_market_date::text,
                      contract_hash,contract,created_at::text
               FROM forward_paper_v2_cohorts ORDER BY created_at DESC"""
        )
        cols = ["cohort_id", "lab_run_id", "policy_name", "mode", "entry_market_date",
                "contract_hash", "contract", "created_at"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _exact_price(conn, ticker: str, market_date: str) -> float | None:
    rows = db.fetch_prices(conn, ticker)
    for row in reversed(rows):
        if row["date"] == market_date:
            value = row.get("adj_close") or row.get("close")
            return float(value) if value else None
        if row["date"] < market_date:
            break
    return None


def latest_common_market_date(conn, tickers: list[str]) -> str | None:
    latest = []
    cached: dict[str, list[dict]] = {}
    for ticker in tickers:
        rows = db.fetch_prices(conn, ticker)
        cached[ticker] = rows
        if not rows:
            return None
        latest.append(rows[-1]["date"])
    candidate = min(latest)
    if all(_exact_price(conn, ticker, candidate) is not None for ticker in tickers):
        return candidate
    return None


def build_mark(conn, cohort: dict, market_date: str | None = None) -> dict:
    contract = cohort["contract"]
    required = sorted(set(contract["longs"] + contract["shorts"] +
                          (contract["frozen_eligible_universe"] if contract["mode"] == "long_only" else [])))
    market_date = market_date or latest_common_market_date(conn, required)
    if not market_date:
        raise ValueError("no complete same-market-date price set exists")
    entry_date = contract["entry_market_date"]
    if market_date < entry_date:
        raise ValueError("mark date precedes cohort entry")
    current: dict[str, float] = {}
    for ticker in required:
        value = _exact_price(conn, ticker, market_date)
        if value is None:
            raise ValueError(f"missing exact {market_date} adjusted close for {ticker}; mark aborted")
        current[ticker] = value

    entry = contract["entry_adjusted_close"]
    returns = {t: (current[t] / float(entry[t]) - 1.0) * 100.0 for t in required}
    long_return = sum(returns[t] for t in contract["longs"]) / len(contract["longs"])
    if contract["mode"] == "long_short":
        short_return = sum(returns[t] for t in contract["shorts"]) / len(contract["shorts"])
        gross = 0.5 * long_return - 0.5 * short_return
        control = 0.0
    else:
        short_return = None
        gross = long_return
        universe = contract["frozen_eligible_universe"]
        control = sum(returns[t] for t in universe) / len(universe)

    # Entry/rebalance cost is charged once to the cohort return, not every mark.
    cost_pct = float(contract["cost_bps"]) / 100.0
    net = gross - cost_pct
    return {
        "schema_version": SCHEMA_VERSION,
        "cohort_id": cohort["cohort_id"],
        "market_date": market_date,
        "entry_market_date": entry_date,
        "gross_return_pct": round(gross, 4),
        "entry_cost_pct": round(cost_pct, 4),
        "net_return_pct": round(net, 4),
        "control_return_pct": round(control, 4),
        "excess_return_pct": round(net - control, 4),
        "long_return_pct": round(long_return, 4),
        "short_return_pct": round(short_return, 4) if short_return is not None else None,
        "coverage": {"required": len(required), "priced": len(current), "complete": True},
        "constituent_adjusted_close": current,
    }


def save_mark(conn, mark: dict) -> None:
    from psycopg.types.json import Jsonb
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO forward_paper_v2_marks (cohort_id,market_date,mark)
               VALUES (%s,%s,%s)
               ON CONFLICT (cohort_id,market_date) DO NOTHING""",
            (mark["cohort_id"], mark["market_date"], Jsonb(mark)),
        )
    conn.commit()


def marks(conn, cohort_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT market_date::text,mark FROM forward_paper_v2_marks
               WHERE cohort_id=%s ORDER BY market_date""",
            (cohort_id,),
        )
        return [{"market_date": d, **(m or {})} for d, m in cur.fetchall()]


def _max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    peak = 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value - peak)
    return round(worst, 2)


def status(cohort: dict, cohort_marks: list[dict], *, today: str | None = None) -> dict:
    today = (today or date.today().isoformat())[:10]
    age = (date.fromisoformat(today) - date.fromisoformat(cohort["entry_market_date"])).days
    valid = [m for m in cohort_marks if (m.get("coverage") or {}).get("complete")]
    excess = [float(m["excess_return_pct"]) for m in valid]
    net = [float(m["net_return_pct"]) for m in valid]
    latest = valid[-1] if valid else None
    gates = {
        "minimum_calendar_days": age >= MIN_CALENDAR_DAYS,
        "minimum_complete_marks": len(valid) >= MIN_MARKS,
        "positive_cumulative_excess": bool(latest and latest["excess_return_pct"] > 0),
        "positive_excess_share": bool(excess and sum(x > 0 for x in excess) / len(excess) >= MIN_POSITIVE_EXCESS_SHARE),
        "drawdown_within_limit": bool(net and (_max_drawdown(net) or 0.0) >= MAX_DRAWDOWN_PCT),
        "complete_coverage_only": len(valid) == len(cohort_marks),
    }
    mature = gates["minimum_calendar_days"] and gates["minimum_complete_marks"]
    if not mature:
        state = "COLLECTING"
    elif all(gates.values()):
        state = "REVIEW_ELIGIBLE"
    else:
        state = "FAILED"
    return {
        "status": state,
        "cohort_id": cohort["cohort_id"],
        "policy_name": cohort["policy_name"], "mode": cohort["mode"],
        "age_calendar_days": age, "complete_marks": len(valid),
        "latest": latest, "gates": gates,
        "max_drawdown_pct": _max_drawdown(net),
        "positive_excess_share": round(sum(x > 0 for x in excess) / len(excess), 3) if excess else None,
        "review_boundary": "REVIEW_ELIGIBLE permits human review only; never live execution",
    }
