"""Select stock vs supported option expression for one P2 portfolio position.

Usage:
  python scripts/select_trade_expression.py TICKER YYYYMM STRIKE1,STRIKE2,...

Requires a persisted P2 portfolio proposal and live/delayed option chain access.
The script only persists a proposal; it never places an order.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.market_data import get_provider
from stock_machine.options import GenerationPolicy, generate_strategies, load_latest_forecast
from stock_machine.portfolio.expression import ExpressionPolicy, select_expression
from stock_machine.portfolio.expression_store import save as save_expression
from stock_machine.portfolio.store import latest as latest_portfolio


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: python scripts/select_trade_expression.py TICKER YYYYMM STRIKE1,STRIKE2,...")
        return 2
    ticker = sys.argv[1].upper()
    month = sys.argv[2].upper()
    strikes = [float(x) for x in sys.argv[3].split(",") if x.strip()]
    if not strikes:
        print("at least one strike is required")
        return 2

    with db.connect() as conn:
        portfolio_row = latest_portfolio(conn)
    if not portfolio_row:
        print(json.dumps({"status": "NO_TRADE", "reason": "no persisted portfolio proposal"}, indent=2))
        return 1
    position = next((p for p in (portfolio_row["proposal"].get("positions") or [])
                     if p.get("ticker") == ticker), None)
    if not position:
        print(json.dumps({"status": "NO_TRADE", "ticker": ticker,
                          "reason": "ticker is not in the latest approved P2 proposal"}, indent=2))
        return 1

    provider = get_provider()
    try:
        chain = provider.option_chain(ticker, month, strikes)
    finally:
        provider.close()

    forecast = load_latest_forecast(ticker)
    generated = generate_strategies(
        chain,
        forecast=forecast,
        policy=GenerationPolicy(
            capital_limit=abs(float(position["weight"])) * 100_000.0,
            allow_delayed=False,
        ),
    )
    result = select_expression(
        position,
        generated.candidates,
        ExpressionPolicy(portfolio_value=100_000.0),
    )
    result["portfolio_proposal_id"] = portfolio_row["proposal_id"]
    result["option_generation"] = {
        "as_of": generated.as_of.isoformat(),
        "candidate_count": len(generated.candidates),
        "rejection_count": len(generated.rejected),
        "warnings": generated.warnings,
    }

    with db.connect() as conn:
        expression_id = save_expression(conn, portfolio_row["proposal_id"], result)
    result["expression_id"] = expression_id
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
