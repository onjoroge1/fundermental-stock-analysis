"""Nightly paper-ledger evaluation: snapshot the agent's paper ledger, score resolved positions,
evaluate once at the pre-registered horizon (paper-ledger-evaluation, item 52).

  python -m scripts.run_paper_ledger_forward

Writes data/paper_ledger_forward/<completed session>.json and prints the receipt.
Exit 1 when attention is needed: a ledger row that changed after its snapshot, a late
snapshot, a policy-version change, an excluded or failed position, a protocol change,
a refused non-main run, failure or contention. Read-only toward the ledger.
"""
import json
import sys
from pathlib import Path

from stock_machine.agent_intelligence.paper_ledger_forward import run_daily


def exit_code(receipt: dict) -> int:
    return 0 if receipt.get("status") == "OK" else 1


def main():
    receipt = run_daily()
    directory = Path("data/paper_ledger_forward")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (receipt["completed_session"] + ".json")).write_text(json.dumps(receipt, indent=2, default=str))
    print(json.dumps(receipt, default=str))
    code = exit_code(receipt)
    if code:
        snapshot = receipt.get("snapshot") or {}
        outcomes = receipt.get("outcomes") or {}
        detail = [str(receipt.get("reason_code") or receipt.get("status")),
                  *(f"ledger row changed {m}" for m in snapshot.get("mutated") or []),
                  *(f"late snapshot {p}" for p in snapshot.get("late") or []),
                  *(f"policy {k} now {v}" for k, v in (receipt.get("policy_drift") or {}).items()),
                  *(f"excluded {e}" for e in outcomes.get("excluded") or []),
                  *(f"error {e.get('position_id') or ''} {e['error']}" for e in outcomes.get("errors") or []),
                  *(receipt.get("sticky_attention") or [])]
        print("::warning::paper-ledger evaluation needs attention: " + "; ".join(detail), file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
