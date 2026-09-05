"""Versioned dataset manifests and trading-readiness checks.

The normalized tables are optimized for serving.  This module creates an
append-only manifest for every ingestion result so a forecast or bundle can be
traced to the exact content that was observed.  Quality is evaluated from the
incoming rows before a manifest is persisted; the web layer only reads those
completed manifests.
"""
from __future__ import annotations

import hashlib
import json
from math import isfinite
from datetime import date
from typing import Any, Iterable
from .market_calendar import price_freshness


REQUIRED_DATASETS = ("fundamentals", "prices", "filings")
ALL_DATASETS = (
    "fundamentals", "prices", "filings", "shares", "consensus",
    "earnings_surprises", "corporate_actions",
)


def content_hash(rows: Iterable[dict]) -> str:
    """Stable content identity independent of retrieval time or row order."""
    canonical_rows = sorted(
        list(rows),
        key=lambda row: json.dumps(row, sort_keys=True, default=str),
    )
    body = json.dumps(
        canonical_rows, sort_keys=True, separators=(",", ":"), default=str,
    ).encode()
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _days_old(value: str | None, as_of: date) -> int | None:
    if not value:
        return None
    return (as_of - date.fromisoformat(str(value)[:10])).days


def _date_bounds(rows: list[dict],
                 keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    values = [str(row[key])[:10] for row in rows for key in keys
              if row.get(key)]
    return (min(values), max(values)) if values else (None, None)


def assess_dataset(dataset: str, rows: list[dict], *,
                   as_of: date | None = None) -> dict[str, Any]:
    """Return a deterministic manifest payload for one normalized dataset."""
    if dataset not in ALL_DATASETS:
        raise ValueError(f"unsupported dataset: {dataset}")
    check_time = as_of
    as_of = as_of or date.today()
    row_count = len(rows)
    status = "PASS"
    reasons: list[str] = []
    metrics: dict[str, Any] = {}

    if dataset == "fundamentals":
        quarters = [r for r in rows if r.get("duration_type") == "quarter"]
        latest = max(quarters, key=lambda r: r.get("period_end") or "") \
            if quarters else None
        critical = ("revenue", "net_income", "diluted_eps",
                    "operating_cash_flow", "total_assets",
                    "shareholders_equity")
        present = sum((latest or {}).get("fields", {}).get(k) is not None
                      for k in critical)
        completeness = round(present / len(critical), 4) if latest else 0.0
        metrics.update({"quarter_count": len(quarters),
                        "critical_completeness": completeness})
        if len(quarters) < 4:
            status = "FAIL"
            reasons.append("fewer than four quarterly periods")
        elif completeness < 1:
            status = "WARN"
            reasons.append("latest quarter is missing critical fields")
    elif dataset == "prices":
        _, newest = _date_bounds(rows, ("date",))
        age = _days_old(newest, as_of)
        incomplete = sum(
            any(r.get(k) is None for k in ("date", "close", "volume"))
            for r in rows
        )
        invalid = sum(not isinstance(r.get("close"), (int, float))
                      or not isfinite(r["close"]) or r["close"] <= 0
                      or (r.get("adj_close") is not None and (not isfinite(r["adj_close"]) or r["adj_close"] <= 0))
                      or (r.get("volume") is not None and (not isfinite(r["volume"]) or r["volume"] < 0))
                      for r in rows)
        duplicate_dates = len(rows) - len({r.get("date") for r in rows})
        metrics.update({"freshness_days": age,
                        "incomplete_rows": incomplete, "invalid_rows": invalid,
                        "duplicate_dates": duplicate_dates,
                        "missing_adjusted_close": sum(not r.get("adj_close") for r in rows)})
        freshness = price_freshness(newest, as_of=check_time)
        metrics.update(freshness)
        if not rows:
            status = "FAIL"
            reasons.append("no price history")
        elif invalid or duplicate_dates:
            status = "FAIL"
            reasons.append(f"{invalid} invalid rows and {duplicate_dates} duplicate dates")
        elif incomplete:
            status = "FAIL"
            reasons.append(f"{incomplete} price rows lack date, close, or volume")
        elif freshness["status"] != "CURRENT":
            status = "WARN"
            reasons.append(f"price data {freshness['status'].lower()}: expected completed session {freshness['expected_market_date']}, have {newest}")
    elif dataset == "filings":
        if not rows:
            status = "FAIL"
            reasons.append("no qualifying SEC filings")
    elif dataset == "shares":
        if not rows:
            status = "WARN"
            reasons.append(
                "no cover-page share history; valuation may use a fallback")
    elif dataset in ("consensus", "earnings_surprises"):
        if not rows:
            status = "PENDING"
            reasons.append("optional vendor dataset is not available")

    date_keys = {
        "fundamentals": ("period_end",), "prices": ("date",),
        "filings": ("filed_at",), "shares": ("as_of",),
        "consensus": ("forecast_period_end",),
        "earnings_surprises": ("date",), "corporate_actions": ("date",),
    }[dataset]
    min_date, max_date = _date_bounds(rows, date_keys)
    return {
        "dataset": dataset,
        "content_hash": content_hash(rows),
        "row_count": row_count,
        "min_record_date": min_date,
        "max_record_date": max_date,
        "status": status,
        "reasons": reasons,
        "metrics": metrics,
    }


def readiness_for_snapshots(snapshots: dict[str, dict], *,
                            as_of: date | None = None) -> dict[str, Any]:
    """Gate trade research on the required point-in-time inputs."""
    check_time = as_of
    as_of = as_of or date.today()
    blockers, warnings = [], []
    for dataset in REQUIRED_DATASETS:
        item = snapshots.get(dataset)
        if not item:
            blockers.append(f"{dataset}: no recorded snapshot")
        elif item.get("status") == "FAIL":
            blockers.extend(f"{dataset}: {reason}"
                            for reason in item.get("reasons") or ["failed"])
        elif item.get("status") == "WARN":
            warnings.extend(f"{dataset}: {reason}"
                            for reason in item.get("reasons") or ["warning"])
        observed_age = _days_old(item.get("last_checked_at") or item.get("observed_at"), as_of) if item else None
        if observed_age is not None and observed_age > 7:
            blockers.append(
                f"{dataset}: manifest has not refreshed for {observed_age} days")
        elif observed_age is not None and observed_age > 3:
            warnings.append(
                f"{dataset}: manifest has not refreshed for {observed_age} days")
        if dataset == "prices" and item:
            fresh = price_freshness(item.get("max_record_date"), as_of=check_time)
            if fresh["status"] != "CURRENT":
                blockers.append(f"prices: {fresh['status'].lower()}; expected {fresh['expected_market_date']}, have {fresh['latest_market_date']}")
    for dataset, item in snapshots.items():
        if dataset not in REQUIRED_DATASETS and item.get("status") == "WARN":
            warnings.extend(f"{dataset}: {reason}"
                            for reason in item.get("reasons") or ["warning"])
    status = "BLOCKED" if blockers else ("CAUTION" if warnings else "READY")
    return {
        "status": status,
        "trade_eligible": not blockers,
        "blockers": blockers,
        "warnings": warnings,
    }


def build_report(companies: list[dict], latest: list[dict], *,
                 as_of: date | None = None) -> dict[str, Any]:
    """Build the read-only cross-ticker dashboard response."""
    as_of = as_of or date.today()
    by_ticker: dict[str, dict[str, dict]] = {}
    for item in latest:
        by_ticker.setdefault(item["ticker"], {})[item["dataset"]] = item
    rows = []
    for company in companies:
        ticker = company["ticker"]
        snapshots = by_ticker.get(ticker, {})
        readiness = readiness_for_snapshots(snapshots, as_of=as_of)
        rows.append({
            "ticker": ticker,
            "legal_name": company.get("legal_name"),
            "readiness": readiness,
            "datasets": snapshots,
        })
    counts = {key: sum(r["readiness"]["status"] == key for r in rows)
              for key in ("READY", "CAUTION", "BLOCKED")}
    return {
        "as_of": as_of.isoformat(),
        "summary": counts,
        "required_datasets": list(REQUIRED_DATASETS),
        "tickers": rows,
        "principle": ("Trade research is eligible only when fundamentals, "
                      "prices, and filings have recorded, non-failing "
                      "point-in-time snapshots."),
    }
