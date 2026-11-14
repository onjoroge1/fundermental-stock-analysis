"""Admission to the experimental paper book: six checks, applied by code, recorded every time.

The Portfolio Manager role owns the decision, but no model makes it. ``evaluate`` is a pure function of the
strategy's inventory record, the book, what is already active, and whether an independent reviewer signed
the evidence off. Its result is saved as an admission decision whether it admits or blocks.

What admission can and cannot do:

* It can create one deployment in state EXPERIMENTAL_PAPER. It cannot create any other state, and no state
  is live. There is no broker here.
* A strategy whose own record says closed, failed, parked or excluded is never admitted. Nor is a record
  under a new name that carries the same frozen files as an excluded one.
* A weak result is admitted as weak: PASS_WEAK is kept on the decision and is never rounded up to PASS.
* Admitting the same version to the same book again returns the deployment that already exists.

Execution is supported only where code exists for it. NATIVE is supported for a strategy version that has
a rule registered in ``paper.RULES``; none is registered for any real strategy. EXTERNAL_LOCAL arrives with
the importer for local records. So every real candidate is still blocked at check 3.
"""
from __future__ import annotations

from psycopg.types.json import Jsonb

from . import config, paper, tasks, tools

ROLE = "portfolio_manager"
POLICY_VERSION = "admission.v1"
SUPPORTED_EXECUTION: frozenset[str] = frozenset()
BLOCKING_DEFECTS = frozenset({"LOOK_AHEAD", "TIMING", "PRICE_BASIS", "ACCOUNTING", "CRITICAL_DATA"})
PROTOCOL_FIELDS = ("registered_on", "horizon", "benchmarks", "maturity", "stop_criteria")
REVIEW_TASK = "review_strategy"
SIGNOFF_VERDICT = "FAIRLY_REPRESENTED"
ADMISSION_LOCK = 0x0FF1CE03
NAMES = ("eligible", "positive net development result, reproducible", "no unresolved critical defect",
         "frozen rules and supported paper execution", "forward protocol recorded first",
         "independent reviewer sign-off", "capacity in the book, not already active")


def _check(number: int, fails: list[str], weak: list[str] = ()) -> dict:
    outcome = "FAIL" if fails else "PASS_WEAK" if weak else "PASS"
    return {"check": number, "name": NAMES[number], "outcome": outcome, "codes": list(fails or weak)}


def evaluate(strategy: dict, *, book: dict | None, active: list[dict], excluded_hashes: set[str],
             signoff: bool, paper_enabled: bool, supported: frozenset[str] | None = None) -> list[dict]:
    """The six checks of the build brief, after an eligibility gate (check 0). Pure: it reads nothing."""
    supported = SUPPORTED_EXECUTION if supported is None else supported
    record = strategy["record"]
    hashes = set((record.get("hashes") or {}).values())
    checks = [_check(0, [code for code, hit in (
        ("ORIGINAL_STATUS_NOT_ADMISSIBLE", not strategy["admissible"]),
        ("SAME_RULES_AS_EXCLUDED_STRATEGY", bool(hashes & excluded_hashes))) if hit])]

    development = record.get("development") or {}
    checks.append(_check(1, [code for code, hit in (
        ("NO_DEVELOPMENT_RESULT", not development),
        ("NOT_NET_POSITIVE", bool(development) and development.get("net_positive") is not True),
        ("NOT_REPRODUCIBLE", bool(development) and development.get("reproducible") is not True)) if hit],
        ["DEVELOPMENT_ONLY"] if strategy["evidence_tier"] == "DEVELOPMENT" or development.get("weak") else []))

    open_defects = [d for d in record.get("defects") or [] if not d.get("resolved")]
    checks.append(_check(2, sorted({"UNRESOLVED_" + d["category"] for d in open_defects
                                    if d.get("category") in BLOCKING_DEFECTS}),
                         sorted({"OPEN_" + str(d.get("category")) for d in open_defects})))

    limits = (book or {}).get("limits") or {}
    checks.append(_check(3, [code for code, hit in (
        ("FROZEN_HASHES_MISSING", not hashes),
        ("COST_MODEL_MISSING", not development.get("cost_model")),
        ("EXECUTION_NOT_SUPPORTED", strategy["execution_kind"] not in supported),
        ("PAPER_BOOK_SWITCHED_OFF", not paper_enabled),
        ("SHORTS_NEED_A_BORROW_POLICY", bool(record.get("shorts")) and not limits.get("borrow_policy"))) if hit]))

    protocol = record.get("forward_protocol") or {}
    checks.append(_check(4, ["FORWARD_PROTOCOL_INCOMPLETE"] if any(not protocol.get(f) for f in PROTOCOL_FIELDS) else []))
    checks.append(_check(5, [] if signoff else ["NO_REVIEWER_SIGNOFF"]))

    groups = {a["risk_group"] for a in active}
    checks.append(_check(6, [code for code, hit in (
        ("NO_BOOK", book is None),
        ("STRATEGY_ALREADY_ACTIVE", any(a["strategy_id"] == strategy["strategy_id"] for a in active)),
        ("RISK_GROUP_ALREADY_ACTIVE", strategy["risk_group"] in groups),
        ("BOOK_FULL", book is not None and len(groups) >= book["max_active_deployments"])) if hit]))
    return checks


def outcome(checks: list[dict]) -> str:
    return "BLOCKED" if any(c["outcome"] == "FAIL" for c in checks) else "ADMITTED"


def create_book(book_id: str, *, starting_equity, max_active_deployments: int, limits: dict, cost_model: dict) -> dict:
    """Record a simulated paper book and its frozen limits. A book is never edited; new limits need a new book."""
    config.require_enabled()
    with tasks.connect() as conn:
        row = conn.execute(
            """INSERT INTO office_books(book_id, starting_equity, max_active_deployments, limits, cost_model,
                   policy_version) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (book_id) DO NOTHING RETURNING book_id""",
            (book_id, starting_equity, max_active_deployments, Jsonb(limits), Jsonb(cost_model),
             POLICY_VERSION)).fetchone()
    return {"book_id": book_id, "created": row is not None}


def admit(strategy_id: str, version: str, *, book_id: str | None = None, reviewer_run_id: str | None = None) -> dict:
    """Apply the checks to one strategy version and record the decision. Creates a deployment only if all pass."""
    config.require_enabled()
    with tasks.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADMISSION_LOCK,))
        strategy = tools.strategy_evidence(ROLE, conn, strategy_id, version)
        if strategy is None:
            raise ValueError("STRATEGY_NOT_IN_INVENTORY")
        book = tools.paper_book(ROLE, conn, book_id) if book_id else None
        if book is not None:
            existing = tools.deployment(ROLE, conn, strategy_id, version, book_id)
            if existing is not None:
                return {"outcome": "ADMITTED", "created": False, **existing}
        active = tools.active_deployments(ROLE, conn, book_id) if book is not None else []
        signoff = bool(reviewer_run_id) and tools.strategy_signoff(
            ROLE, conn, reviewer_run_id, strategy_id, version, task_type=REVIEW_TASK, verdict=SIGNOFF_VERDICT)
        paper_on = config.paper_enabled()
        supported = SUPPORTED_EXECUTION | ({"NATIVE"} if (strategy_id, version) in paper.RULES else set())
        checks = evaluate(strategy, book=book, active=active, signoff=signoff, paper_enabled=paper_on,
                          excluded_hashes=tools.excluded_rule_hashes(ROLE, conn), supported=frozenset(supported))
        result = outcome(checks)
        digest = tools.sha256({"record": strategy["record_sha256"], "book": book, "active": active,
                               "signoff": reviewer_run_id if signoff else None, "paper": paper_on,
                               "supported": sorted(supported), "policy": POLICY_VERSION})
        decision = tools.write_admission_decision(
            ROLE, conn, strategy_id=strategy_id, version=version, book_id=book_id if book is not None else None,
            policy_version=POLICY_VERSION, outcome=result, checks=checks, input_sha256=digest,
            reviewer_run_id=reviewer_run_id if signoff else None)
        out = {"outcome": result, "decision_id": decision["decision_id"], "checks": checks, "created": False}
        if result == "ADMITTED":
            deployed = tools.write_deployment_state(
                ROLE, conn, strategy=strategy, book_id=book_id, state="EXPERIMENTAL_PAPER",
                decision_id=decision["decision_id"])
            out.update(deployed)
    return out
