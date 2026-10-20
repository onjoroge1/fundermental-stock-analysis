"""Run the heuristic direction replay (read-only) and write its evidence.

  python -m scripts.run_direction_replay [--suite direction|signals|fairvalue|events|volatility] [--stage dev|holdout] [--tickers AAPL,MSFT]

direction: the heuristic direction replay (protocol heuristic-direction-replay.v1).
signals:   earnings surprise, estimate revisions, reverse-DCF value gap and the
           value gap timed by technicals (protocol signal-replay.v2).
fairvalue: solid companies traded toward a P/E fair value over 60/120 sessions
           (protocol fair-value-convergence.v1).
events:    daily event strategies with path exits (protocol event-strategy-replay.v1);
           --stage dev (2016-2023, descriptive) or holdout (once, confirmatory).
volatility: 21-session realized-volatility forecasts against simple rules
           (protocol volatility-forecast.v1); --stage dev or holdout as above.

Reads prices, filings and companies from DATABASE_URL in a read-only session;
writes data/direction_replay/<run date>.json (protocol, result, every row) and
prints the result. It never writes to the database or changes trading.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db  # noqa: E402
from stock_machine.agents.contracts import AGENT_UNIVERSE  # noqa: E402
from stock_machine.backtest import (direction_replay, event_replay, fair_value_replay, signal_replay,  # noqa: E402
                                    volatility_replay)
from stock_machine.market_calendar import latest_completed_session  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickers", help="comma-separated; default the agent universe")
    parser.add_argument("--suite", choices=("direction", "signals", "fairvalue", "events", "volatility"),
                        default="direction")
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev", help="events and volatility suites")
    args = parser.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else sorted(AGENT_UNIVERSE)
    with db.connect() as conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        if args.suite in ("events", "volatility"):
            module = event_replay if args.suite == "events" else volatility_replay
            out = module.run(conn, tickers=tickers, stage=args.stage, end=latest_completed_session())
        else:
            module = {"signals": signal_replay, "fairvalue": fair_value_replay}.get(args.suite, direction_replay)
            out = module.run(conn, end=latest_completed_session(), tickers=tickers)
    directory = Path("data/direction_replay")
    directory.mkdir(parents=True, exist_ok=True)
    suffix = f"-{args.stage}" if args.suite in ("events", "volatility") else ""
    path = directory / f"{args.suite}{suffix}-{date.today().isoformat()}.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out.get("result", out), indent=2, default=str))
    print(f"rows: {len(out.get('rows') or out.get('trades') or [])} -> {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
