"""Run the one-time check of the three EXT-FADE.v1 overlays on 2020-01..2023-12.

  python scripts/run_ext_fade_overlays.py check    the one run, after EXT-FADE.v1's own check is in the lab's log
  python scripts/run_ext_fade_overlays.py log      what the overlays' log holds

(stock_machine/backtest/ext_fade_overlays.py; registered in
docs/evidence/ext-fade-overlays-registration-2026-10-24.md)

The run order is fixed: first EXT-FADE.v1's one check, unchanged (python scripts/run_intraday_lab.py check --variants
EXT-FADE.v1), then this, once. It refuses unless the module (on disk and as imported), this runner (on disk and as
started) and the IV/HV files are the registered ones, the registration records the owner's choices and they are the
specs', the lab's code is the code of v1's logged check, and docs/evidence/ext-fade-overlays-log.json holds no check
at all. It replays v1 on the check months with the lab's own replay (unlogged) and refuses unless the trade count and
the mean net at 10 bps equal v1's logged check exactly; only then are the overlays applied to those trades. It appends
one entry per overlay to the log before it returns, then writes
data/ext_fade_overlays/check-2020-01-2023-12-entry-<n>.{json,md} (aggregates only).

It opens no bar month after 2023-12, and the overlays' own daily rows and IV/HV series are cut at 2023-12-31. The
daily price rows go to the lab's replay uncut, exactly as in v1's check: the lab's printing-error test reads the next
session's open, so the last 2023 session's eligibility reads the 2024-01-02 open.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stock_machine.agents.contracts import AGENT_UNIVERSE  # noqa: E402
from stock_machine.backtest import ext_fade_overlays as ov  # noqa: E402

RUNNER_SHA = ov.file_sha(__file__)      # this runner as it started: refused if the file changes before the check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check")
    p.add_argument("--dev-root", default=str(ROOT / "data/ibkr_intraday/v1"))
    p.add_argument("--prices-dir", default=str(ROOT / "data/ibkr_prices/2026-10-20"))
    p.add_argument("--iv-dir", default=str(ROOT / "data/ibkr_iv/2026-10-20"))
    sub.add_parser("log")
    args = parser.parse_args()
    if args.command == "log":
        log = ov.read_log()
        print(f"{len(log)} entries, {sum(1 for e in log if e['split'] == 'check')} checks")
        for e in log:
            print(f"#{e['entry']} {e['at']} {e['split']} {e['months'][0]}..{e['months'][1]} {e['spec_id']:16} "
                  f"{e['verdict']}  ({e['reading']})")
        return 0
    out = ov.run_check(dev_root=args.dev_root, prices_dir=args.prices_dir, iv_dir=args.iv_dir,
                       tickers=sorted(AGENT_UNIVERSE), runner_path=Path(__file__).resolve(),
                       runner_sha=RUNNER_SHA)
    if out["status"] != "OK":
        print(json.dumps(out, indent=1), file=sys.stderr)
        return 2
    directory = ROOT / "data" / "ext_fade_overlays"
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / f"check-{out['months'][0]}-{out['months'][1]}-entry-{out['log_entries'][0]['entry']:04d}"
    stem.with_suffix(".json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str))
    stem.with_suffix(".md").write_text(ov.render(out))
    print(ov.render(out))
    print(f"logged {len(out['log_entries'])} entries in {ov.LOG_PATH}; wrote {stem}.json and .md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
