"""Fail-closed execution handoff; absence of v2 is not permission to run v1."""
from __future__ import annotations


def paper_instruction(result: dict | None, mode: str) -> dict | None:
    if mode != "PAPER":
        return None
    instruction = (result or {}).get("paper_instruction")
    if ((result or {}).get("status") not in {"UNAVAILABLE", "FAILED"}
            and isinstance(instruction, dict)
            and instruction.get("source") == "agent-intelligence.v2"
            and instruction.get("desired_side") in {"LONG", "SHORT", "FLAT"}):
        return instruction
    return {"desired_side": "FLAT", "source": "agent-intelligence.v2",
            "selected_action": "NO_TRADE", "blocker": "INTELLIGENCE_V2_UNAVAILABLE"}
