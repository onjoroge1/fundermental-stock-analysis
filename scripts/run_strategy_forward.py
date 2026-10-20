"""Daily strategy forward shadow: freeze the latest session's signals, then record completed trades.

  python -m scripts.run_strategy_forward

Writes data/strategy_forward/<completed session>.json and prints the receipt.
Exit 1 when attention is needed, so the scheduled job and the health watchdog
surface it: a freeze waiting for data or SEC filings, a missed or late freeze,
invalid timing, a blocked or failed trade, a decision-code change, a loss
recorded in the last week (an isolated partial session of at most three stocks
is listed but does not fail the run), a protocol change, a refused non-main
run, failure or contention.
"""
import json
import sys
from pathlib import Path

from stock_machine.agent_intelligence.strategy_forward import OK_FREEZE, run_daily


def exit_code(receipt: dict) -> int:
    if receipt.get("status") != "OK":
        return 1
    return 0 if (receipt.get("freeze") or {}).get("status") in OK_FREEZE else 1


def main():
    receipt = run_daily()
    directory = Path("data/strategy_forward")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (receipt["completed_session"] + ".json")).write_text(json.dumps(receipt, indent=2, default=str))
    print(json.dumps(receipt, default=str))
    code = exit_code(receipt)
    if code:
        freeze = receipt.get("freeze") or {}
        outcomes = receipt.get("outcomes") or {}
        detail = [str(receipt.get("reason_code") or freeze.get("status")),
                  *(f"newly missed {m}" for m in freeze.get("newly_missed") or []),
                  *(f"SEC filings unread {t}" for t in freeze.get("sec_unavailable") or []),
                  *(f"invalid timing {m}" for m in outcomes.get("invalid") or []),
                  *(f"blocked {b}" for b in outcomes.get("blocked") or []),
                  *(f"trade error {e.get('chain') or ''} {e['error']}" for e in outcomes.get("errors") or []),
                  *(receipt.get("sticky_attention") or [])]
        print("::warning::strategy forward shadow needs attention: " + "; ".join(detail), file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
