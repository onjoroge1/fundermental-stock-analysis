"""Power and size of the pre-registered promotion tests (tracker item 21).

  python scripts/power_promotion_tests.py power   [--sims 100 --workers 10]
  python scripts/power_promotion_tests.py rules   [--sims 200 --null-sims 500 --workers 10]
  python scripts/power_promotion_tests.py calibrate   # read-only, needs DATABASE_URL

`power` runs the live evaluation code on synthetic panels; `rules` compares
the current decision rule with a Newey-West + t alternative; `calibrate`
derives detectable differences from real matured records.
"""
import argparse
import json

from stock_machine.agent_intelligence import power


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("power", "rules", "calibrate"))
    parser.add_argument("--sims", type=int, default=100)
    parser.add_argument("--null-sims", type=int, default=500)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    if args.mode == "power":
        result = power.power_table(sims=args.sims, workers=args.workers,
                                   bootstrap_samples=args.bootstrap)
    elif args.mode == "rules":
        result = power.rule_comparison(sims=args.sims, null_sims=args.null_sims,
                                       draws=args.bootstrap, workers=args.workers)
    else:
        from stock_machine import db

        with db.connect() as conn:
            conn.execute("SET TRANSACTION READ ONLY")
            result = power.calibrate(conn)
    print(json.dumps(result, indent=1, default=str))


if __name__ == "__main__":
    main()
