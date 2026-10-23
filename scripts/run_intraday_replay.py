"""Run the intraday first-hour replay locally (protocol intraday-first-hour.v1).

  python scripts/run_intraday_replay.py --stage dev [--dev-root data/ibkr_intraday/v1]
      [--holdout-root data/ibkr_intraday/v1-holdout] [--prices-dir data/ibkr_prices/2026-10-20]

Inputs are local IBKR files (scripts/fetch_ibkr_intraday.py and scripts/fetch_ibkr_prices.py) and the pinned SEC
earnings dataset. The hold-out stage refuses to run until the protocol, the replay code and both input sets are
registered in stock_machine/backtest/intraday_replay.py; both stages refuse an unfinished fetch (preview the finished
months with scripts/report_intraday_replay.py). The development stage also waits for the intraday lab's one-time looks
(LAB_LOOKS_PENDING, intraday_lab.replay_reads_through): it reads months the lab still holds back. Writes data/intraday_replay/<stage>-<run date>.json: aggregate
statistics and input hashes only (no bars or per-trade prices), the file to record as evidence. Render its summary
with scripts/report_intraday_replay.py render.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine.agents.contracts import AGENT_UNIVERSE  # noqa: E402
from stock_machine.backtest import intraday_lab, intraday_replay  # noqa: E402


def _sci(x) -> str:
    return "-" if x is None else f"{x:+.2e}"


def summary(out: dict) -> str:
    """A console digest; the committed summary is scripts/report_intraday_replay.py render."""
    lines = [f"{out['label']} {out['months'][0]}..{out['months'][1]}: {out['sessions']} sessions, "
             f"{out['eligible_stock_days']} eligible stock-days (book values per eligible stock-day)",
             f"{'':4} {'trades':>7} {'reward':>10} {'lower':>10} {'upper':>10} {'skill':>10} {'s.lower':>10} "
             f"{'net bps':>8} {'verdict':<22} {'labels'}"]
    for name, r in out["strategies"].items():
        if not r["coverage"]["trades"]:
            lines.append(f"{name:4} {0:>7} no trades{'':>51} {r.get('verdict', '-'):<22}")
            continue
        labels = r["labels"]
        shown = ("-" if not (r["reward"]["lower"] or 0) > 0 else
                 labels["robustness"] + (", COST_SENSITIVE" if labels.get("cost_sensitive") else ""))
        net = r["per_trade"]["mean_net_bps"]
        lines.append(f"{name:4} {r['coverage']['trades']:>7} {_sci(r['reward']['mean']):>10} "
                     f"{_sci(r['reward']['lower']):>10} {_sci(r['reward']['upper']):>10} "
                     f"{_sci(r['direction_skill']['mean']):>10} {_sci(r['direction_skill']['lower']):>10} "
                     f"{'-' if net is None else f'{net:+.1f}':>8} {r.get('verdict', '-'):<22} {shown}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--dev-root", default="data/ibkr_intraday/v1")
    parser.add_argument("--holdout-root", default="data/ibkr_intraday/v1-holdout")
    parser.add_argument("--prices-dir", default="data/ibkr_prices/2026-10-20")
    args = parser.parse_args()
    limit = intraday_lab.replay_reads_through()
    if args.stage == "dev" and limit is not None:
        print(json.dumps({"status": "REFUSED", "reason": "LAB_LOOKS_PENDING", "lab_reads_through": limit,
                          "why": "the development stage reads 2016-2023; the lab holds back the months after "
                                 f"{limit} until its one-time confirms and checks are logged"}, indent=1), file=sys.stderr)
        return 2
    out = intraday_replay.run(dev_root=args.dev_root, holdout_root=args.holdout_root, prices_dir=args.prices_dir,
                              stage=args.stage, tickers=sorted(AGENT_UNIVERSE))
    if out["status"] != "OK":
        print(json.dumps(out, indent=1), file=sys.stderr)
        return 2
    directory = Path("data/intraday_replay")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{args.stage}-{date.today().isoformat()}.json"
    path.write_text(json.dumps(out, indent=1, sort_keys=True, default=str))
    print(summary(out))
    print(f"wrote {path} (aggregates only)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
