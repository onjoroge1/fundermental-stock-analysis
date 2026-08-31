"""Read-optimized decision dashboard aggregation.

The dashboard consumes persisted evidence only. It deliberately does not fan
out into live option-chain requests; PR35 recommendations remain on-demand per
ticker so a dashboard refresh cannot overload the broker bridge.
"""
from __future__ import annotations

from typing import Any

from . import db


def _latest_expressions(conn, proposal_id: str | None) -> list[dict]:
    if not proposal_id:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT ticker, expression_id, created_at::text, status, result
                 FROM trade_expression_proposals
                WHERE portfolio_proposal_id=%s
                ORDER BY ticker, created_at DESC""",
            (proposal_id,),
        )
        rows = cur.fetchall()
    seen: set[str] = set()
    out = []
    for ticker, expression_id, created_at, status, result in rows:
        symbol = str(ticker or "").upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append({
            "ticker": symbol,
            "expression_id": expression_id,
            "created_at": created_at,
            "status": status,
            "result": result or {},
        })
    return out


def _research_map(conn) -> dict[str, dict]:
    try:
        from .control_plane import research_index
        rows = research_index(conn)
    except Exception:
        conn.rollback()
        rows = []
    return {
        str(row.get("ticker") or "").upper(): row
        for row in rows if row.get("ticker")
    }


def _rank_opportunities(research: dict[str, dict]) -> dict[str, list[dict]]:
    scored = []
    for ticker, row in research.items():
        report = row.get("report_12m") or {}
        expected = report.get("expected_return_pct")
        if expected is None:
            continue
        scored.append({
            "ticker": ticker,
            "sector": row.get("sector"),
            "price": row.get("price"),
            "expected_return_12m_pct": float(expected),
            "quality_score": row.get("composite_score"),
            "classification": report.get("classification"),
            "data_quality_status": row.get("data_quality_status"),
        })
    bullish = sorted(scored, key=lambda row: (-row["expected_return_12m_pct"], row["ticker"]))[:10]
    bearish = sorted(scored, key=lambda row: (row["expected_return_12m_pct"], row["ticker"]))[:10]
    return {"bullish": bullish, "bearish": bearish}


def _position_rows(conn, proposal: dict | None, expressions: list[dict],
                   research: dict[str, dict]) -> list[dict]:
    if not proposal:
        return []
    expression_by_ticker = {row["ticker"]: row for row in expressions}
    rows = []
    for position in (proposal.get("proposal") or {}).get("positions") or []:
        ticker = str(position.get("ticker") or "").upper()
        if not ticker:
            continue
        report = db.latest_report(conn, ticker) or {}
        thesis = report.get("investment_thesis") or {}
        conclusion = report.get("conclusion") or {}
        indexed = research.get(ticker) or {}
        expression = expression_by_ticker.get(ticker)
        rows.append({
            "ticker": ticker,
            "sector": position.get("sector") or indexed.get("sector"),
            "weight": position.get("weight"),
            "direction": "LONG" if float(position.get("weight") or 0) > 0 else "SHORT",
            "expected_excess_return_pct": position.get("expected_excess_return_pct"),
            "prob_outperform": position.get("prob_outperform"),
            "realized_vol": position.get("realized_vol"),
            "beta": position.get("beta"),
            "stock_expected_return_12m_pct": (indexed.get("report_12m") or {}).get("expected_return_pct"),
            "data_quality_status": indexed.get("data_quality_status"),
            "classification": conclusion.get("classification") or (indexed.get("report_12m") or {}).get("classification"),
            "thesis_summary": thesis.get("summary"),
            "invalidation_conditions": thesis.get("invalidation_conditions") or [],
            "trade_expression": None if expression is None else expression.get("result"),
            "option_recommendation_url": f"/api/v1/options/{ticker}/recommendation?direction={'bullish' if float(position.get('weight') or 0) > 0 else 'bearish'}&horizon=12m",
            "research_url": f"/api/v1/stocks/{ticker}/research",
        })
    return rows


def _strategy_lab(conn) -> dict:
    try:
        from .strategy_lab_v2_store import latest
        row = latest(conn)
    except Exception as exc:
        conn.rollback()
        return {"status": "PENDING", "reason": f"{type(exc).__name__}: {exc}"}
    if not row:
        return {"status": "PENDING", "reason": "no Strategy Lab v2 run"}
    eligible = {}
    for mode, mode_row in ((row.get("result") or {}).get("modes") or {}).items():
        eligible[mode] = [
            name for name, item in (mode_row.get("strategies") or {}).items()
            if (item.get("promotion") or {}).get("status") == "ELIGIBLE_FOR_FORWARD_PAPER_REVIEW"
        ]
    return {
        "status": "OK",
        "run_id": row.get("run_id"),
        "as_of": row.get("as_of"),
        "created_at": row.get("created_at"),
        "eligible": eligible,
        "p2_current_policy": (row.get("result") or {}).get("p2_current_policy"),
    }


def _forward_paper(conn) -> dict:
    try:
        from .forward_paper_v2 import list_cohorts, marks, status
        cohorts = list_cohorts(conn)
        rows = []
        for cohort in cohorts:
            cohort_marks = marks(conn, cohort["cohort_id"])
            rows.append({
                "cohort_id": cohort["cohort_id"],
                "policy_name": cohort["policy_name"],
                "mode": cohort["mode"],
                "entry_market_date": cohort["entry_market_date"],
                "longs": (cohort.get("contract") or {}).get("longs") or [],
                "shorts": (cohort.get("contract") or {}).get("shorts") or [],
                "incubation": status(cohort, cohort_marks),
            })
        return {"status": "OK" if rows else "PENDING", "cohorts": rows}
    except Exception as exc:
        conn.rollback()
        return {"status": "PENDING", "reason": f"{type(exc).__name__}: {exc}", "cohorts": []}


def _automation_health() -> dict:
    try:
        from .automation import queue_health
        return queue_health()
    except Exception as exc:
        return {"status": "PENDING", "reason": f"{type(exc).__name__}: {exc}"}


def build_dashboard() -> dict[str, Any]:
    with db.connect() as conn:
        try:
            from .portfolio.store import latest as latest_portfolio
            portfolio = latest_portfolio(conn)
        except Exception as exc:
            conn.rollback()
            portfolio = {"status": "PENDING", "reason": f"{type(exc).__name__}: {exc}"}
        proposal_id = portfolio.get("proposal_id") if portfolio else None
        expressions = _latest_expressions(conn, proposal_id) if proposal_id else []
        research = _research_map(conn)
        positions = _position_rows(conn, portfolio if proposal_id else None, expressions, research)
        strategy_lab = _strategy_lab(conn)
        forward_paper = _forward_paper(conn)

    opportunity = _rank_opportunities(research)
    exposures = ((portfolio or {}).get("proposal") or {}).get("exposures") or {}
    return {
        "status": "OK",
        "generated_from_persisted_evidence": True,
        "live_option_calls_on_page_load": False,
        "portfolio": {
            "status": "OK" if proposal_id else "PENDING",
            "proposal_id": proposal_id,
            "created_at": (portfolio or {}).get("created_at"),
            "horizon_days": ((portfolio or {}).get("proposal") or {}).get("horizon_days"),
            "exposures": exposures,
            "position_count": len(positions),
            "positions": positions,
        },
        "opportunities": opportunity,
        "research_index": {
            "indexed_count": len(research),
            "note": "opportunity rankings use the compact research index only; pending names remain visible in /api/v1/universe",
        },
        "strategy_lab": strategy_lab,
        "forward_paper": forward_paper,
        "automation": _automation_health(),
        "safety": {
            "live_trade_execution": False,
            "option_recommendations_on_demand_only": True,
            "forward_paper_promotion_automatic": False,
        },
    }
