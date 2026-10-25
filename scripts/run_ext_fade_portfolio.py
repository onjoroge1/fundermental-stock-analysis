"""Run the one-time check of EXT-FADE-CB.v1 (EXT-FADE.v1 on a constrained book) on 2020-01..2023-12.

  python scripts/run_ext_fade_portfolio.py check    the one run, after v1's check and the overlays' check are logged
  python scripts/run_ext_fade_portfolio.py log      what this check's log holds

(stock_machine/backtest/ext_fade_portfolio.py; registered in
docs/evidence/ext-fade-portfolio-registration-2026-10-25.md)

The run order is fixed: (1) EXT-FADE.v1's one check, unchanged (python scripts/run_intraday_lab.py check --variants
EXT-FADE.v1); (2) the three overlays' one check (python scripts/run_ext_fade_overlays.py check); (3) this, once. It
refuses unless the module (on disk and as imported), this runner (on disk and as started), the spec, its parameters
and its sector map are the registered ones, the lab's code is the code of v1's logged check, the overlays' code is
the registered one and their check is logged, and docs/evidence/ext-fade-portfolio-log.json holds no check at all.
It replays v1 on the check months with the lab's own replay (unlogged), refuses unless the trade count and the mean
net at 10 bps equal v1's logged check exactly and its own pass over the stock-days agrees with the lab's eligibility,
candidates and every trade, then sizes and hedges the book. It appends one entry to the log before it returns, then
writes data/ext_fade_portfolio/check-2020-01-2023-12-entry-<n>.{json,md} (aggregates only).

It opens no bar month after 2023-12. The daily price rows go uncut to the lab's replay and to the module's stock-day
pass, exactly as in v1's check: the lab's printing-error test reads the next session's open, so the last 2023
session's eligibility reads the 2024-01-02 open. No IV or HV data is read.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stock_machine.agents.contracts import AGENT_UNIVERSE  # noqa: E402
from stock_machine.backtest import ext_fade_portfolio as cb  # noqa: E402

RUNNER_SHA = cb.file_sha(__file__)      # this runner as it started: refused if the file changes before the check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check")
    p.add_argument("--dev-root", default=str(ROOT / "data/ibkr_intraday/v1"))
    p.add_argument("--prices-dir", default=str(ROOT / "data/ibkr_prices/2026-10-20"))
    sub.add_parser("log")
    args = parser.parse_args()
    if args.command == "log":
        log = cb.read_log()
        print(f"{len(log)} entries, {sum(1 for e in log if e['split'] == 'check')} checks")
        for e in log:
            print(f"#{e['entry']} {e['at']} {e['split']} {e['months'][0]}..{e['months'][1]} {e['spec_id']} "
                  f"{e['verdict']}  ({e['reading']})")
        return 0
    out = cb.run_check(dev_root=args.dev_root, prices_dir=args.prices_dir, tickers=sorted(AGENT_UNIVERSE),
                       runner_path=Path(__file__).resolve(), runner_sha=RUNNER_SHA)
    if out["status"] != "OK":
        print(json.dumps(out, indent=1), file=sys.stderr)
        return 2
    directory = ROOT / "data" / "ext_fade_portfolio"
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / f"check-{out['months'][0]}-{out['months'][1]}-entry-{out['log_entry']['entry']:04d}"
    stem.with_suffix(".json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str))
    stem.with_suffix(".md").write_text(cb.render(out))
    print(cb.render(out))
    print(f"logged entry {out['log_entry']['entry']} in {cb.LOG_PATH}; wrote {stem}.json and .md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
