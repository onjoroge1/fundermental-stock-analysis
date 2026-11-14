"""Office roles and what each one is allowed to do.

A role is a fixed list of named tools. The tools themselves arrive in later changes; this module fixes,
ahead of them, who may read what and who may write what, so that a later change cannot quietly widen a role.

Separation rules, each enforced by a test:

* No tool gives a shell, free SQL, GitHub administration, credentials, arbitrary web access or a broker.
* The author of a memo cannot review it: the analyst cannot write reviews, the reviewer cannot write memos.
* Only the Portfolio Manager records admission decisions and deployment states.
* QA and the reviewer read the paper book and evidence but cannot change strategies, fills or orders.
* No role writes an order or a fill. Simulated orders and fills are produced by code (the execution
  service), never by a model.
* Only roles that reason over text may call a model; the controller and scanner are code only.

Every tool call goes through ``authorize``. An unknown role or an unlisted tool is refused.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import tasks

READ, WRITE = "read", "write"

# name -> (kind, the single office record a write tool may create). Read tools return typed data only.
TOOLS: dict[str, tuple[str, str | None]] = {
    "read_events": (READ, None),
    "read_filing": (READ, None),
    "read_fundamentals": (READ, None),
    "read_prices": (READ, None),
    "read_memo": (READ, None),
    "read_review": (READ, None),
    "read_strategy_evidence": (READ, None),
    "read_paper_book": (READ, None),
    "read_outcomes": (READ, None),
    "read_office_status": (READ, None),
    "write_event": (WRITE, "office_events"),
    "write_memo": (WRITE, "office_memos"),
    "write_review": (WRITE, "office_reviews"),
    "write_admission_decision": (WRITE, "office_admission_decisions"),
    "write_deployment_state": (WRITE, "office_deployments"),
    "write_opportunity": (WRITE, "office_opportunities"),
    "write_evaluation": (WRITE, "office_evaluations"),
    "write_proposal": (WRITE, "office_proposals"),
    "write_briefing": (WRITE, "office_briefings"),
}

# Words that must never appear in a tool name, whoever adds it.
FORBIDDEN_WORDS = ("shell", "bash", "exec", "sql", "query", "github", "secret", "credential", "token", "key",
                   "env", "http", "fetch", "url", "browse", "broker", "ibkr", "order", "fill", "trade",
                   "live", "deploy_code", "migrate", "delete", "update")


class ToolNotAllowed(PermissionError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Role:
    name: str
    version: int
    purpose: str
    tools: tuple[str, ...]
    may_call_model: bool

    def definition(self) -> dict:
        """The content recorded, immutably, in ``office_agent_definitions``."""
        return {"purpose": self.purpose, "tools": sorted(self.tools), "may_call_model": self.may_call_model}


ROLES: dict[str, Role] = {r.name: r for r in (
    Role("controller", 1, "Queue, dependencies, retries, budgets and deadlines. Code only.",
         ("read_office_status",), False),
    Role("scout", 1, "Turn new filings and events into deduplicated office events.",
         ("read_events", "read_filing", "write_event"), False),
    Role("analyst", 1, "Write a memo of facts, interpretations, counter-evidence and citations.",
         ("read_events", "read_filing", "read_fundamentals", "read_prices", "read_memo", "read_review",
          "write_memo"), True),
    Role("reviewer", 1, "Check a memo's claims against primary sources and argue the opposing case.",
         ("read_events", "read_filing", "read_fundamentals", "read_prices", "read_memo", "read_review",
          "read_strategy_evidence", "write_review"), True),
    Role("portfolio_manager", 1, "Apply the admission checks and keep the paper strategy registry.",
         ("read_strategy_evidence", "read_review", "read_paper_book", "read_outcomes",
          "write_admission_decision", "write_deployment_state", "write_briefing"), True),
    Role("scanner", 1, "Run frozen rules over data and record every opportunity seen. Code only.",
         ("read_prices", "read_fundamentals", "read_strategy_evidence", "read_paper_book",
          "write_opportunity"), False),
    Role("qa", 1, "Score outcomes independently and propose new, separately versioned experiments.",
         ("read_strategy_evidence", "read_paper_book", "read_outcomes", "read_memo", "read_review",
          "write_evaluation", "write_proposal", "write_briefing"), True),
)}


def authorize(role: str, tool: str) -> None:
    """Raise unless ``role`` may use ``tool``. Called before every tool runs."""
    if role not in ROLES:
        raise ToolNotAllowed("UNKNOWN_ROLE")
    if tool not in TOOLS:
        raise ToolNotAllowed("UNKNOWN_TOOL")
    if tool not in ROLES[role].tools:
        raise ToolNotAllowed("TOOL_NOT_ALLOWED_FOR_ROLE")


def writes(role: str) -> set[str]:
    """The office tables a role can add rows to."""
    return {TOOLS[t][1] for t in ROLES[role].tools if TOOLS[t][0] == WRITE}


def register_all(provider: str = "none", model: str = "") -> list[dict]:
    """Record every role's current definition. Safe to repeat; a changed definition needs a new version."""
    return [tasks.register_definition(r.name, r.version, provider if r.may_call_model else "none",
                                      model if r.may_call_model else "", r.definition())
            for r in ROLES.values()]
