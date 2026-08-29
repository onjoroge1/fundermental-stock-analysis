"""Build and persist a review-only P2 portfolio proposal."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.portfolio import PortfolioPolicy, build_proposal
from stock_machine.portfolio.store import save


def _rows(rows):
    return [{"date": r["date"], "close": r.get("close"),
             "adj_close": r.get("adj_close") or r.get("close")}
            for r in rows if r.get("adj_close") is not None or r.get("close") is not None]


def main() -> int:
    with db.connect() as conn:
        companies = db.list_companies(conn)
        spy = _rows(db.fetch_prices(conn, "SPY"))
        candidates = []
        for company in companies:
            ticker = company["ticker"]
            forecast = db.latest_prediction_forecast(conn, ticker)
            if not forecast:
                continue
            prices = _rows(db.fetch_prices(conn, ticker))
            latest_date = prices[-1]["date"] if prices else None
            if not latest_date or forecast.get("as_of") != latest_date:
                continue
            candidates.append({
                "ticker": ticker,
                "sector": company.get("sector"),
                "forecast": forecast,
                "price_rows": prices,
            })

    proposal = build_proposal(candidates, spy, PortfolioPolicy())
    proposal["candidate_count"] = len(candidates)
    with db.connect() as conn:
        proposal_id = save(conn, proposal)
    print(json.dumps({"proposal_id": proposal_id, **proposal}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
