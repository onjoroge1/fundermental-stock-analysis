"""Run the heuristic direction replay (read-only) and write its evidence.

  python -m scripts.run_direction_replay [--suite direction|signals] [--tickers AAPL,MSFT]

direction: the heuristic direction replay (protocol heuristic-direction-replay.v1).
signals:   earnings surprise, estimate revisions, reverse-DCF value gap and the
           value gap timed by technicals (protocol signal-replay.v1).

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
from stock_machine.backtest import direction_replay, signal_replay  # noqa: E402
from stock_machine.market_calendar import latest_completed_session  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickers", help="comma-separated; default the agent universe")
    parser.add_argument("--suite", choices=("direction", "signals"), default="direction")
    args = parser.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else sorted(AGENT_UNIVERSE)
    with db.connect() as conn:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        module = signal_replay if args.suite == "signals" else direction_replay
        out = module.run(conn, end=latest_completed_session(), tickers=tickers)
    directory = Path("data/direction_replay")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{args.suite}-{date.today().isoformat()}.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out["result"], indent=2, default=str))
    print(f"rows: {len(out['rows'])} -> {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
