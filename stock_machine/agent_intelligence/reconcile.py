"""Realized paper ledger versus counterfactual learning labels, read-only.

The pooled model learns from fixed-window counterfactual outcomes, while the
paper ledger records what simulated execution actually did. For a position
opened under the prospective contract, both describe the same entry close,
side and 20-session target, so their gross returns should agree. A late exit
(the holding-limit job ran after the target) is reported separately; any
other disagreement means the learning labels and the ledger diverged.
"""

from __future__ import annotations

from math import isfinite

VERSION = "ledger-counterfactual-reconciliation.v1"
# Absolute gross-return tolerance in percentage points: adjusted-price
# vintages can move by a cent between the fill and the later scoring read.
TOLERANCE_PCT = 0.05
SIDES = {"LONG": "LONG_STOCK", "SHORT": "SHORT_STOCK"}


def _number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and isfinite(value)
    )


def classify(position: dict, label: dict | None) -> dict:
    """position: ledger fields; label: the counterfactual reward record payload."""
    base = {
        "position_id": position["position_id"],
        "ticker": position["ticker"],
        "decision_id": position["source_decision_id"],
        "side": position["side"],
        "entry_market_date": position["entry_market_date"],
        "exit_market_date": position["exit_market_date"],
    }
    if not label:
        return {**base, "status": "AWAITING_LABEL"}
    outcome = ((label.get("arms") or {}).get(SIDES.get(position["side"], "")) or {}).get("outcome") or {}
    notional = position.get("entry_notional_usd")
    values = (
        notional,
        position.get("realized_pnl_usd"),
        position.get("entry_cost_usd"),
        position.get("exit_cost_usd"),
        outcome.get("gross_return_pct"),
    )
    if not all(_number(v) for v in values) or notional <= 0:
        return {**base, "status": "INVALID_INPUTS"}
    # Costs are added back: the label's gross return excludes them too.
    realized = (
        position["realized_pnl_usd"] + position["entry_cost_usd"] + position["exit_cost_usd"]
    ) / notional * 100
    row = {
        **base,
        "label_entry_date": outcome.get("entry_date"),
        "label_exit_date": outcome.get("exit_date"),
        "realized_gross_return_pct": round(realized, 6),
        "label_gross_return_pct": outcome["gross_return_pct"],
        "difference_pct": round(realized - outcome["gross_return_pct"], 6),
    }
    if outcome.get("entry_date") != position["entry_market_date"]:
        return {**row, "status": "ENTRY_MISMATCH"}
    if outcome.get("exit_date") != position["exit_market_date"]:
        return {**row, "status": "LATE_EXIT"}
    if abs(row["difference_pct"]) > TOLERANCE_PCT:
        return {**row, "status": "RETURN_MISMATCH"}
    return {**row, "status": "MATCHED"}


def summary(conn, *, limit: int = 500) -> dict:
    """Most recent closed prospective positions; bounded and read-only."""
    if not 1 <= limit <= 5000:
        raise ValueError("RECONCILE_LIMIT_INVALID")
    if conn.execute("SELECT to_regclass('agent_paper_positions')").fetchone()[0] is None:
        return {"schema_version": VERSION, "status": "UNAVAILABLE_NO_PAPER_LEDGER", "rows": []}
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT p.position_id::text,p.ticker,p.side,p.source_decision_id::text,
                      p.entry_market_date::text,p.exit_market_date::text,
                      p.entry_notional_usd,p.entry_cost_usd,p.exit_cost_usd,p.realized_pnl_usd,
                      r.payload AS label
            FROM agent_paper_positions p
            JOIN agent_trade_intents i ON i.intent_id=p.source_intent_id
            LEFT JOIN research_evidence_records r
              ON r.kind='AGENT_REWARD_V3' AND r.request_key=p.source_decision_id::text
             AND r.payload->>'learning_basis'='PROSPECTIVE_COUNTERFACTUAL_V1'
            WHERE p.status='CLOSED'
              AND i.risk_snapshot->>'execution_contract'='prospective-next-close.v2'
              AND i.risk_snapshot ? 'holding_commitment'
            ORDER BY p.exit_market_date DESC,p.position_id LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
    results = []
    for row in rows:
        label = row.pop("label")
        for key in ("entry_notional_usd", "entry_cost_usd", "exit_cost_usd", "realized_pnl_usd"):
            if row[key] is not None:
                row[key] = float(row[key])
        results.append(classify(row, label))
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    problems = counts.get("RETURN_MISMATCH", 0) + counts.get("ENTRY_MISMATCH", 0) + counts.get("INVALID_INPUTS", 0)
    return {
        "schema_version": VERSION,
        "status": "ATTENTION" if problems else ("OK" if results else "AWAITING_CLOSED_POSITIONS"),
        "tolerance_pct": TOLERANCE_PCT,
        "checked": len(results),
        "counts": counts,
        "problems": [r for r in results if r["status"] in {"RETURN_MISMATCH", "ENTRY_MISMATCH", "INVALID_INPUTS"}][:50],
        "late_exits": [r for r in results if r["status"] == "LATE_EXIT"][:50],
        "broker_submission": False,
    }
