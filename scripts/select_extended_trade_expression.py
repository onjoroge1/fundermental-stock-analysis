"""Review one calendar/diagonal against the current P2 portfolio target.

Usage:
  python scripts/select_extended_trade_expression.py \
      TICKER NEAR_MONTH FAR_MONTH C|P NEAR_STRIKE FAR_STRIKE

The command reads live option chains, builds the mixed-expiration valuation,
applies P2-D path risk plus P2-E event intelligence, and persists only the
review proposal. It never places an order.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.events.screen import build_strategy_event_screen
from stock_machine.market_data import get_provider
from stock_machine.options.extended import mixed_expiration
from stock_machine.portfolio.expression import ExpressionPolicy
from stock_machine.portfolio.expression_store import save as save_expression
from stock_machine.portfolio.extended_expression import compare_extended
from stock_machine.portfolio.store import latest as latest_portfolio


def main() -> int:
    if len(sys.argv) != 7:
        print(
            "usage: python scripts/select_extended_trade_expression.py "
            "TICKER NEAR_MONTH FAR_MONTH C|P NEAR_STRIKE FAR_STRIKE"
        )
        return 2
    ticker = sys.argv[1].upper()
    near_month = sys.argv[2].upper()
    far_month = sys.argv[3].upper()
    right = sys.argv[4].upper()
    near_strike = float(sys.argv[5])
    far_strike = float(sys.argv[6])
    if right not in {"C", "P"}:
        print("option right must be C or P")
        return 2

    with db.connect() as conn:
        portfolio_row = latest_portfolio(conn)
    if not portfolio_row:
        print(json.dumps({
            "status": "NO_TRADE",
            "reason": "no persisted P2 portfolio proposal",
        }, indent=2))
        return 1
    position = next(
        (p for p in (portfolio_row["proposal"].get("positions") or [])
         if p.get("ticker") == ticker),
        None,
    )
    if not position:
        print(json.dumps({
            "status": "NO_TRADE",
            "ticker": ticker,
            "reason": "ticker is not in the latest P2 portfolio proposal",
        }, indent=2))
        return 1

    provider = get_provider()
    try:
        near_chain = provider.option_chain(ticker, near_month, [near_strike])
        far_chain = provider.option_chain(ticker, far_month, [far_strike])
    finally:
        provider.close()

    candidate = mixed_expiration(
        near_chain, far_chain, near_strike, far_strike, right=right
    )

    with db.connect() as conn:
        result = compare_extended(
            position,
            [candidate],
            ExpressionPolicy(portfolio_value=100_000.0),
            event_screen_factory=lambda c: build_strategy_event_screen(
                conn, ticker, c
            ),
        )

    result["portfolio_proposal_id"] = portfolio_row["proposal_id"]
    result["candidate"] = {
        "strategy_type": candidate.get("strategy_type"),
        "near_expiration": candidate.get("near_expiration"),
        "far_expiration": candidate.get("far_expiration"),
        "near_strike": near_strike,
        "far_strike": far_strike,
        "valuation_mode": candidate.get("valuation_mode"),
    }

    with db.connect() as conn:
        expression_id = save_expression(
            conn, portfolio_row["proposal_id"], result
        )
    result["expression_id"] = expression_id
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
