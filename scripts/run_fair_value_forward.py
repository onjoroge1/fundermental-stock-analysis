"""Daily fair-value forward tracking: score matured months, freeze the month-end.

  python -m scripts.run_fair_value_forward

Writes data/fair_value_forward/<completed session>.json and prints the
receipt. Exit 1 when attention is needed (a freeze waiting for data, a missed
freeze, invalid timing, a protocol change, failure or contention), so the
scheduled job and the health watchdog surface it.
"""
import json
import sys
from pathlib import Path

from stock_machine.agent_intelligence.fair_value_forward import run_daily

OK_FREEZE = ("NOTHING_DUE", "FROZEN", "REPLAYED", "MISSED_ALREADY_RECORDED")


def exit_code(receipt: dict) -> int:
    if receipt.get("status") != "OK":
        return 1
    return 0 if (receipt.get("freeze") or {}).get("status") in OK_FREEZE else 1


def main():
    receipt = run_daily()
    directory = Path("data/fair_value_forward")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (receipt["completed_session"] + ".json")).write_text(json.dumps(receipt, indent=2, default=str))
    print(json.dumps(receipt, default=str))
    code = exit_code(receipt)
    if code:
        freeze = receipt.get("freeze") or {}
        outcomes = receipt.get("outcomes") or {}
        detail = [str(receipt.get("reason_code") or freeze.get("status")),
                  *(f"newly missed {m}" for m in freeze.get("newly_missed") or []),
                  *(f"invalid timing {m}" for m in outcomes.get("invalid") or []),
                  *([f"scoring error {outcomes['error']}"] if outcomes.get("error") else []),
                  *(receipt.get("sticky_attention") or [])]
        print("::warning::fair-value forward needs attention: " + "; ".join(detail), file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
