"""Run the intraday first-hour replay locally (protocol intraday-first-hour.v1).

  python scripts/run_intraday_replay.py --stage dev [--dev-root data/ibkr_intraday/v1]
      [--holdout-root data/ibkr_intraday/v1-holdout] [--prices-dir data/ibkr_prices/2026-10-20]

Inputs are local IBKR files (scripts/fetch_ibkr_intraday.py and scripts/fetch_ibkr_prices.py) and the pinned SEC
earnings dataset. The hold-out stage refuses to run until the protocol, the replay code and both input sets are
registered in stock_machine/backtest/intraday_replay.py. Writes data/intraday_replay/<stage>-<run date>.json:
aggregate statistics and input hashes only (no bars or per-trade prices), the file to record as evidence.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine.agents.contracts import AGENT_UNIVERSE  # noqa: E402
from stock_machine.backtest import intraday_replay  # noqa: E402


def _fmt(x, digits=3):
    return "-" if x is None else f"{x:+.{digits}f}"


def summary(out: dict) -> str:
    lines = [f"{out['label']} {out['months'][0]}..{out['months'][1]}: {out['sessions']} sessions, "
             f"{out['eligible_stock_days']} eligible stock-days",
             f"{'':4} {'trades':>7} {'reward':>8} {'lower':>8} {'upper':>8} {'skill':>8} {'s.lower':>8} "
             f"{'net bps':>8} {'verdict':<22} {'label'}"]
    for name, r in out["strategies"].items():
        lines.append(f"{name:4} {r['coverage']['trades']:>7} {_fmt(r['reward']['mean'], 4):>8} "
                     f"{_fmt(r['reward']['lower'], 4):>8} {_fmt(r['reward']['upper'], 4):>8} "
                     f"{_fmt(r['direction_skill']['mean'], 4):>8} {_fmt(r['direction_skill']['lower'], 4):>8} "
                     f"{_fmt(r['per_trade']['mean_net_bps'], 1):>8} {r.get('verdict', '-'):<22} "
                     f"{r['labels']['robustness']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--dev-root", default="data/ibkr_intraday/v1")
    parser.add_argument("--holdout-root", default="data/ibkr_intraday/v1-holdout")
    parser.add_argument("--prices-dir", default="data/ibkr_prices/2026-10-20")
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="development only: run on a partial fetch, labelled a preview")
    args = parser.parse_args()
    if args.allow_incomplete and args.stage != "dev":
        raise SystemExit("--allow-incomplete is for development previews only")
    out = intraday_replay.run(dev_root=args.dev_root, holdout_root=args.holdout_root, prices_dir=args.prices_dir,
                              stage=args.stage, tickers=sorted(AGENT_UNIVERSE), allow_incomplete=args.allow_incomplete)
    if out["status"] != "OK":
        print(json.dumps(out, indent=1), file=sys.stderr)
        return 2
    directory = Path("data/intraday_replay")
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{args.stage}-preview" if out.get("missing_ticker_months") else args.stage
    path = directory / f"{stem}-{date.today().isoformat()}.json"
    path.write_text(json.dumps(out, indent=1, sort_keys=True, default=str))
    print(summary(out))
    print(f"wrote {path} (aggregates only)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
