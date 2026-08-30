"""Explicitly freeze current baskets for eligible Strategy Lab v2 policies.

This command never runs from a schedule. A human/operator chooses when to sync.
Repeated syncs reuse the same policy/basket cohort instead of resetting its age.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_machine import db
from stock_machine.forward_paper_v2 import build_contract, current_cross_section, sync_cohort
from stock_machine.strategy_lab_v2_store import latest as latest_lab


def main() -> int:
    requested_mode = sys.argv[1] if len(sys.argv) > 1 else None
    requested_policy = sys.argv[2] if len(sys.argv) > 2 else None

    with db.connect() as conn:
        lab = latest_lab(conn)
        if not lab:
            print(json.dumps({"status": "BLOCKED", "reason": "no Strategy Lab v2 run exists"}))
            return 1
        observations, prices = current_cross_section(conn)
        if len(observations) < 8:
            print(json.dumps({"status": "BLOCKED", "reason": "current PIT cross-section has fewer than 8 eligible names"}))
            return 1

        eligible = []
        for mode, mode_result in (lab["result"].get("modes") or {}).items():
            if requested_mode and mode != requested_mode:
                continue
            for policy, row in (mode_result.get("strategies") or {}).items():
                if requested_policy and policy != requested_policy:
                    continue
                if (row.get("promotion") or {}).get("status") == "ELIGIBLE_FOR_FORWARD_PAPER_REVIEW":
                    eligible.append((mode, policy))

        if not eligible:
            print(json.dumps({"status": "BLOCKED", "reason": "no requested Strategy Lab v2 policy is eligible for forward paper review"}))
            return 1

        results = []
        failures = []
        for mode, policy in eligible:
            try:
                contract = build_contract(lab, policy, mode, observations, prices)
                synced = sync_cohort(conn, contract)
                results.append({
                    "mode": mode, "policy": policy,
                    "action": synced["action"], "cohort_id": synced["cohort_id"],
                    "entry_market_date": synced["contract"]["entry_market_date"],
                    "longs": synced["contract"]["longs"],
                    "shorts": synced["contract"]["shorts"],
                })
            except Exception as exc:
                failures.append({"mode": mode, "policy": policy,
                                 "error": f"{type(exc).__name__}: {exc}"})

    print(json.dumps({
        "status": "OK" if not failures else "PARTIAL",
        "lab_run_id": lab["run_id"],
        "current_observations": len(observations),
        "cohorts": results, "failures": failures,
    }, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
