"""Daily fair-value forward tracking: freeze a due month-end, then score matured months.

  python -m scripts.run_fair_value_forward

Writes data/fair_value_forward/<completed session>.json and prints the
receipt. Exit 1 when attention is needed, so the scheduled job and the health
watchdog surface it: a freeze waiting for data, a missed or late freeze,
invalid timing, a scoring error, a decision-code change, a loss recorded in
the last week, a protocol change, a refused non-main run, failure or
contention.
"""
import json
import sys
from pathlib import Path

from stock_machine.agent_intelligence.fair_value_forward import OK_FREEZE, run_daily


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
                  *(f"frozen without bars for {', '.join(freeze.get('missing_bars_at_freeze'))}"
                    for _ in [0] if freeze.get("missing_bars_at_freeze")),
                  *(f"invalid timing {m}" for m in outcomes.get("invalid") or []),
                  *(f"outcome blocked by missing bars {m}" for m in outcomes.get("blocked") or []),
                  *(f"stocks excluded for missing bars {m}" for m in outcomes.get("excluded") or []),
                  *(f"tracked source missing {p}" for p in receipt.get("missing_sources") or []),
                  *(f"scoring error {e.get('decision_session') or ''} {e['error']}" for e in outcomes.get("errors") or []),
                  *(receipt.get("sticky_attention") or [])]
        print("::warning::fair-value forward needs attention: " + "; ".join(detail), file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
