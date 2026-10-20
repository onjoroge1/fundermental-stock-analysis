"""Run the implied-volatility comparison locally (protocol implied-vol-comparison.v1).

  python scripts/run_implied_vol_replay.py --prices-dir data/ibkr_prices/<date> --iv-dir data/ibkr_iv/<date> [--stage dev|holdout]

Inputs are local IBKR files (scripts/fetch_ibkr_prices.py and
scripts/probe_ibkr_iv_history.py) and the pinned SEC earnings dataset. Writes
data/implied_vol_replay/<stage>-<run date>.json (every stock-month; stays
local, since it contains IBKR values) and
data/implied_vol_replay/<stage>-<run date>-aggregate.json (only aggregate
statistics and input hashes: the file to record as evidence).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine.agents.contracts import AGENT_UNIVERSE  # noqa: E402
from stock_machine.backtest import implied_vol_replay  # noqa: E402
from stock_machine.market_calendar import latest_completed_session  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prices-dir", required=True)
    parser.add_argument("--iv-dir", required=True)
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev")
    args = parser.parse_args()
    out = implied_vol_replay.run(prices_dir=args.prices_dir, iv_dir=args.iv_dir, stage=args.stage,
                                 end=latest_completed_session(), tickers=sorted(AGENT_UNIVERSE))
    directory = Path("data/implied_vol_replay")
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{args.stage}-{date.today().isoformat()}"
    (directory / f"{stem}.json").write_text(json.dumps(out, indent=1, default=str))
    aggregate = {"protocol_sha256": implied_vol_replay.PROTOCOL_SHA256, "result": out.get("result", out)}
    (directory / f"{stem}-aggregate.json").write_text(json.dumps(aggregate, indent=1, sort_keys=True, default=str))
    print(json.dumps(out.get("result", out), indent=1, default=str)[:6000])
    print(f"wrote {directory}/{stem}.json (local) and {stem}-aggregate.json (aggregates only)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
