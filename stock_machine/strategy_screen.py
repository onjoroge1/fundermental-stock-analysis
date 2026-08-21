"""Current, paper-only selections from policies promoted by Strategy Lab."""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from . import db
from .backtest.engine import TickerData
from .data_quality import build_report
from .strategy_lab import (
    DEFAULT_COST_BPS,
    MIN_NAMES,
    STRATEGIES,
    TOP_FRACTION,
    _value,
    score_policy_rows,
)


SCHEMA_VERSION = "strategy_screen.v2"


def current_observations(conn, *, as_of: str) -> tuple[list[dict], dict[str, dict]]:
    """Build current factor rows and the independent point-in-time quality gate."""
    companies = db.list_companies(conn)
    quality_report = build_report(
        companies, db.latest_dataset_snapshots(conn),
        as_of=date.fromisoformat(as_of[:10]),
    )
    readiness = {row["ticker"]: row["readiness"]
                 for row in quality_report["tickers"]}
    observations = []
    for company in companies:
        ticker = company["ticker"]
        row = TickerData(conn, ticker).observe(as_of[:10])
        if row is None:
            continue
        prices = db.fetch_prices(conn, ticker, as_of[:10])
        latest = prices[-1] if prices else {}
        row.update({
            "price": latest.get("adj_close") or latest.get("close"),
            "price_date": latest.get("date"),
        })
        observations.append(row)
    return observations, readiness


def eligible_policy_names(lab: dict) -> list[str]:
    if lab.get("status") != "OK":
        return []
    return sorted(
        name for name, result in (lab.get("strategies") or {}).items()
        if result.get("kind") == "multi_factor"
        and (result.get("promotion") or {}).get("status") == "PAPER_ELIGIBLE"
        and name in STRATEGIES
    )


def generate(lab: dict, observations: list[dict], readiness: dict[str, dict],
             *, as_of: str | None = None) -> dict[str, Any]:
    """Apply only promoted policies to a quality-gated current cross-section."""
    as_of = (as_of or date.today().isoformat())[:10]
    policies = eligible_policy_names(lab)
    if not policies:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "BLOCKED",
            "execution_status": "PAPER_ONLY",
            "as_of": as_of,
            "reason": "no Strategy Lab policy is PAPER_ELIGIBLE",
            "policies": {},
        }

    eligible_rows, excluded = [], []
    for row in observations:
        quality = readiness.get(row["ticker"], {})
        if not quality.get("trade_eligible", False):
            excluded.append({
                "ticker": row["ticker"],
                "reason": "; ".join(quality.get("blockers") or
                                     ["point-in-time data is not trade eligible"]),
            })
        else:
            eligible_rows.append(row)
    if len(eligible_rows) < MIN_NAMES:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "BLOCKED",
            "execution_status": "PAPER_ONLY",
            "as_of": as_of,
            "reason": (f"need {MIN_NAMES}+ quality-eligible current names; "
                       f"have {len(eligible_rows)}"),
            "universe": {"observed": len(observations),
                         "eligible": len(eligible_rows), "excluded": excluded},
            "policies": {},
        }

    output: dict[str, dict] = {}
    by_ticker = {row["ticker"]: row for row in eligible_rows}
    for name in policies:
        definition = STRATEGIES[name]
        scored = score_policy_rows(eligible_rows, definition)
        if scored is None:
            output[name] = {
                "label": definition["label"],
                "status": "INSUFFICIENT_SIGNAL_COVERAGE",
                "candidates": [],
            }
            continue
        count = max(2, math.ceil(len(eligible_rows) * TOP_FRACTION))
        ordered = sorted(scored["scores"].items(),
                         key=lambda item: (-item[1], item[0]))
        candidates = []
        for rank, (ticker, score) in enumerate(ordered[:count], start=1):
            row = by_ticker[ticker]
            candidates.append({
                "ticker": ticker,
                "rank": rank,
                "score": round(score, 6),
                "target_weight": round(1 / count, 6),
                "signal_percentiles": scored["signal_percentiles"][ticker],
                "raw_signals": {
                    ".".join(x for x in path if x is not None): _value(row, path)
                    for path in definition["signals"]
                },
                "price": row.get("price"),
                "price_date": row.get("price_date"),
                "data_readiness": readiness[ticker].get("status"),
                "warnings": readiness[ticker].get("warnings") or [],
            })
        output[name] = {
            "label": definition["label"],
            "status": "PAPER_ELIGIBLE",
            "signals": [".".join(x for x in path if x is not None)
                        for path in definition["signals"]],
            "selection_rule": f"top {TOP_FRACTION:.0%}, equal weight",
            "candidates": candidates,
        }
    failed = [name for name, result in output.items()
              if result["status"] != "PAPER_ELIGIBLE"]
    if failed:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "BLOCKED",
            "execution_status": "PAPER_ONLY",
            "as_of": as_of,
            "reason": ("promoted policies lack current signal coverage: "
                       + ", ".join(failed)),
            "universe": {"observed": len(observations),
                         "eligible": len(eligible_rows), "excluded": excluded},
            "policies": output,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "OK",
        "execution_status": "PAPER_ONLY",
        "as_of": as_of,
        "policy_source": "latest non-stale Strategy Lab run",
        "cost_bps_per_turnover": (lab.get("config") or {}).get(
            "cost_bps_per_turnover", DEFAULT_COST_BPS),
        "universe": {"observed": len(observations),
                     "eligible": len(eligible_rows), "excluded": excluded},
        "benchmark": [
            {"ticker": row["ticker"], "price": row.get("price"),
             "price_date": row.get("price_date")}
            for row in sorted(eligible_rows, key=lambda item: item["ticker"])
        ],
        "policies": output,
        "limitations": [
            "Selections inherit Strategy Lab survivorship and model risk.",
            "PAPER_ELIGIBLE permits simulation only; it is not a live order signal.",
            "Prices are end-of-day marks and may differ from executable prices.",
        ],
    }
