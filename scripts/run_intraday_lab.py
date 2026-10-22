"""Run the intraday lab: explore variants on 2016-2019, check survivors once on 2020-2023 (stock_machine/backtest/intraday_lab.py).

  python scripts/run_intraday_lab.py explore [--variants GU-LIM.v1,GD-REV.v1]   finished months of 2016-01..2019-12
  python scripts/run_intraday_lab.py check --variants GD-REV.v1                  all of 2020-01..2023-12, once each
  python scripts/run_intraday_lab.py log                                         what the log holds

Reads the local IBKR bars (data/ibkr_intraday/v1) and daily files; never 2024 or later. Each run first appends one
entry per variant to docs/evidence/intraday-lab-log.json (the committed record of every variant tried), then writes
data/intraday_lab/<split>-<first>-<last>.{json,md} (aggregates only).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stock_machine.agents.contracts import AGENT_UNIVERSE  # noqa: E402
from stock_machine.backtest import intraday_lab as lab  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("explore", "check"):
        p = sub.add_parser(name)
        p.add_argument("--variants", default=",".join(lab.VARIANTS) if name == "explore" else None,
                       required=name == "check")
        p.add_argument("--dev-root", default=str(ROOT / "data/ibkr_intraday/v1"))
        p.add_argument("--prices-dir", default=str(ROOT / "data/ibkr_prices/2026-10-20"))
    sub.add_parser("log")
    args = parser.parse_args()
    if args.command == "log":
        log = lab.read_log()
        print(f"{len(log)} entries, {lab.variants_tried(log)} variant tries")
        for e in log:
            print(f"#{e['entry']} {e['at']} {e['split']:7} {e['months'][0]}..{e['months'][1]} {e['variant']:12} "
                  f"trades {e['trades']:>5}  net bps at 10 {e['net_bps_at_10']}")
        return 0
    out = lab.run(args.command, [v.strip() for v in args.variants.split(",") if v.strip()], dev_root=args.dev_root,
                  prices_dir=args.prices_dir, tickers=sorted(AGENT_UNIVERSE))
    if out["status"] != "OK":
        print(json.dumps(out, indent=1), file=sys.stderr)
        return 2
    directory = ROOT / "data" / "intraday_lab"
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / f"{out['split']}-{out['months'][0]}-{out['months'][1]}"
    stem.with_suffix(".json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str))
    stem.with_suffix(".md").write_text(lab.render(out))
    print(lab.render(out))
    print(f"logged {len(out['log_entries'])} entries in {lab.LOG_PATH}; wrote {stem}.json and .md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
