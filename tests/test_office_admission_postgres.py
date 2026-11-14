"""Real PostgreSQL checks for the strategy inventory, admission decisions and deployments.

The qualifying candidate here is a test record in a disposable schema. It is never part of the checked-in
inventory, and supported execution is switched on only inside these tests.
"""
import copy
from uuid import uuid4

import psycopg
import pytest

from stock_machine.office import admission, config, inventory, roles, tasks, tools
from tests.test_office_research_postgres import pg, sql  # noqa: F401

SUPPORTED = frozenset({"NATIVE"})


def fixture_row(strategy_id="FIXTURE-TREND", version="v1", risk_group=None, **changes):
    row = copy.deepcopy(next(r for r in inventory.read() if r["strategy_id"] == "QM-1"))
    row.update(strategy_id=strategy_id, version=version, source_repository="app", execution_kind="NATIVE",
               risk_group=risk_group or strategy_id, hashes={"rules.md": uuid4().hex * 2})
    row.update(changes)
    return row


def signoff(strategy_id="FIXTURE-TREND", version="v1", verdict="FAIRLY_REPRESENTED", task_type="review_strategy"):
    """A completed reviewer run that signed this strategy version off."""
    tasks.enqueue(f"review-strategy:{strategy_id}:{version}:{uuid4().hex}", task_type)
    lease = tasks.claim("reviewer", task_types=(task_type,), max_concurrent=1000)
    tasks.complete(lease, {"strategy_id": strategy_id, "version": version, "verdict": verdict})
    return lease.run_id


@pytest.fixture
def ready(pg, monkeypatch):
    """The paper switch on, one book, and native execution supported: only inside this test."""
    monkeypatch.setenv("OFFICE_PAPER_ENABLED", "true")
    monkeypatch.setattr(admission, "SUPPORTED_EXECUTION", SUPPORTED)
    admission.create_book("experimental", starting_equity=100000, max_active_deployments=2,
                          limits={"max_position_pct": 5}, cost_model={"bps_per_side": 10})
    return pg


def counts(pg):
    return sql(pg, "SELECT (SELECT count(*) FROM office_admission_decisions), (SELECT count(*) FROM office_deployments)")[0]


def test_the_inventory_loads_once_and_a_stored_record_cannot_be_changed(pg):
    assert inventory.load() == {"records": 28, "created": 28}
    assert inventory.load() == {"records": 28, "created": 0}
    rows = dict(sql(pg, "SELECT strategy_id, original_status FROM office_strategy_versions"))
    assert rows["EXT-FADE"] == "Closed, FAIL" and rows["EXT-FADE-X2"] == "Parked" and len(rows) == 28
    assert sql(pg, "SELECT count(*) FROM office_strategy_versions WHERE admissible")[0][0] == 4
    # the same version with a kinder label is refused, in code and in the database
    relabelled = [{**r, "original_status": "Promising"} if r["strategy_id"] == "EXT-FADE" else r for r in inventory.read()]
    with pytest.raises(ValueError, match="INVENTORY_VERSION_ALREADY_RECORDED_WITH_DIFFERENT_CONTENT:EXT-FADE:v1"):
        inventory.load(relabelled)
    for statement in ("UPDATE office_strategy_versions SET admissible = true WHERE strategy_id = 'EXT-FADE'",
                      "UPDATE office_strategy_versions SET original_status = 'Promising'",
                      "DELETE FROM office_strategy_versions", "TRUNCATE office_strategy_versions CASCADE"):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="written once"):
            sql(pg, statement)
    assert dict(sql(pg, "SELECT strategy_id, original_status FROM office_strategy_versions"))["EXT-FADE"] == "Closed, FAIL"


def test_every_real_candidate_is_blocked_today_and_the_decision_is_recorded_once(pg):
    inventory.load()
    for strategy_id, version in (("X2-FLOW", "v1"), ("X2-STRESS", "v1"), ("QM-1", "default:OBS-QM-3"),
                                 ("fair-value-convergence.v1:sector_timed_120", "forward.v1")):
        first = admission.admit(strategy_id, version)
        again = admission.admit(strategy_id, version)
        assert first["outcome"] == "BLOCKED" and again["decision_id"] == first["decision_id"]
    assert counts(pg) == (4, 0)
    checks = sql(pg, "SELECT checks FROM office_admission_decisions WHERE strategy_id = 'X2-FLOW'")[0][0]
    assert [c["outcome"] for c in checks] == ["PASS", "PASS_WEAK", "PASS_WEAK", "FAIL", "PASS", "FAIL", "FAIL"]
    with pytest.raises(ValueError, match="STRATEGY_NOT_IN_INVENTORY"):
        admission.admit("X2-FLOW", "v9")


def test_a_qualifying_test_candidate_gets_exactly_one_deployment_however_often_admission_runs(ready):
    inventory.load([fixture_row()])
    run = signoff()
    first = admission.admit("FIXTURE-TREND", "v1", book_id="experimental", reviewer_run_id=run)
    assert first["outcome"] == "ADMITTED" and first["created"] and first["state"] == "EXPERIMENTAL_PAPER"
    for _ in range(3):
        again = admission.admit("FIXTURE-TREND", "v1", book_id="experimental", reviewer_run_id=run)
        assert again["outcome"] == "ADMITTED" and not again["created"]
        assert again["deployment_id"] == first["deployment_id"]
    assert counts(ready) == (1, 1)
    row = sql(ready, "SELECT state, execution_kind, book_id, admission_decision_id::text, risk_group FROM office_deployments")[0]
    assert row == ("EXPERIMENTAL_PAPER", "NATIVE", "experimental", first["decision_id"], "FIXTURE-TREND")
    decision = sql(ready, "SELECT outcome, reviewer_run_id::text, policy_version, checks FROM office_admission_decisions")[0]
    assert decision[:3] == ("ADMITTED", run, "admission.v1")
    assert [c["outcome"] for c in decision[3]][1:3] == ["PASS_WEAK", "PASS_WEAK"]       # still labelled weak


def test_a_sign_off_counts_only_if_it_is_a_completed_review_of_exactly_this_version(ready):
    inventory.load([fixture_row(), fixture_row("FIXTURE-OTHER")])
    wrong = (None, str(uuid4()), signoff("FIXTURE-OTHER"), signoff(verdict="NOT_FAIRLY_REPRESENTED"),
             signoff(version="v2"), signoff(task_type="analyst_memo"))
    for run in wrong:
        result = admission.admit("FIXTURE-TREND", "v1", book_id="experimental", reviewer_run_id=run)
        assert result["outcome"] == "BLOCKED"
        assert [c["codes"] for c in result["checks"] if c["outcome"] == "FAIL"] == [["NO_REVIEWER_SIGNOFF"]]
    assert counts(ready)[1] == 0
    assert sql(ready, "SELECT count(*) FROM office_admission_decisions WHERE reviewer_run_id IS NOT NULL")[0][0] == 0


def test_near_copies_share_one_place_and_a_full_book_admits_no_more(ready):
    inventory.load([fixture_row("FIXTURE-A", risk_group="GAP-FADE"), fixture_row("FIXTURE-B", risk_group="GAP-FADE"),
                    fixture_row("FIXTURE-C"), fixture_row("FIXTURE-D"), fixture_row("FIXTURE-A", "v2", "GAP-FADE")])

    def go(strategy_id, version="v1"):
        result = admission.admit(strategy_id, version, book_id="experimental",
                                 reviewer_run_id=signoff(strategy_id, version))
        return result["outcome"], [code for c in result["checks"] if c["outcome"] == "FAIL" for code in c["codes"]]

    assert go("FIXTURE-A") == ("ADMITTED", [])
    assert go("FIXTURE-B") == ("BLOCKED", ["RISK_GROUP_ALREADY_ACTIVE"])
    assert go("FIXTURE-A", "v2") == ("BLOCKED", ["STRATEGY_ALREADY_ACTIVE", "RISK_GROUP_ALREADY_ACTIVE"])
    assert go("FIXTURE-C") == ("ADMITTED", [])
    assert go("FIXTURE-D") == ("BLOCKED", ["BOOK_FULL"])
    assert sorted(r[0] for r in sql(ready, "SELECT strategy_id FROM office_deployments")) == ["FIXTURE-A", "FIXTURE-C"]


def test_a_closed_strategy_cannot_be_admitted_under_its_own_or_a_new_label(ready):
    closed_hash = "cd" * 32
    closed = fixture_row("FIXTURE-CLOSED", admissible=False, execution_kind="NONE", original_status="Closed, FAIL",
                         exclusion_reason="Failed its one-time check.", hashes={"rules.md": closed_hash})
    inventory.load([closed, fixture_row("FIXTURE-RENAMED", hashes={"same-rules.md": closed_hash}), fixture_row()])
    for strategy_id, code in (("FIXTURE-CLOSED", "ORIGINAL_STATUS_NOT_ADMISSIBLE"),
                              ("FIXTURE-RENAMED", "SAME_RULES_AS_EXCLUDED_STRATEGY")):
        result = admission.admit(strategy_id, "v1", book_id="experimental", reviewer_run_id=signoff(strategy_id))
        assert result["outcome"] == "BLOCKED" and code in result["checks"][0]["codes"]
    assert counts(ready)[1] == 0
    assert sql(ready, "SELECT original_status FROM office_strategy_versions WHERE strategy_id = 'FIXTURE-CLOSED'") == [
        ("Closed, FAIL",)]


def test_the_paper_switch_and_supported_execution_are_both_required(pg, monkeypatch):
    inventory.load([fixture_row()])
    admission.create_book("experimental", starting_equity=100000, max_active_deployments=2, limits={}, cost_model={})
    run = signoff()
    blocked = admission.admit("FIXTURE-TREND", "v1", book_id="experimental", reviewer_run_id=run)
    assert [c["codes"] for c in blocked["checks"] if c["outcome"] == "FAIL"] == [
        ["EXECUTION_NOT_SUPPORTED", "PAPER_BOOK_SWITCHED_OFF"]]
    monkeypatch.setattr(admission, "SUPPORTED_EXECUTION", SUPPORTED)
    assert admission.admit("FIXTURE-TREND", "v1", book_id="experimental", reviewer_run_id=run)["outcome"] == "BLOCKED"
    monkeypatch.setenv("OFFICE_PAPER_ENABLED", "true")
    assert admission.admit("FIXTURE-TREND", "v1", book_id="experimental", reviewer_run_id=run)["outcome"] == "ADMITTED"
    assert counts(pg) == (3, 1)                                       # each change of inputs is its own decision
    monkeypatch.setenv("OFFICE_ENABLED", "false")
    for call in (inventory.load, lambda: admission.admit("FIXTURE-TREND", "v1"),
                 lambda: admission.create_book("b", starting_equity=1, max_active_deployments=1, limits={}, cost_model={})):
        with pytest.raises(config.OfficeDisabled):
            call()


def test_the_database_refuses_a_deployment_without_its_own_admitted_decision_and_any_live_state(ready):
    inventory.load([fixture_row(), fixture_row("FIXTURE-OTHER")])
    blocked = admission.admit("FIXTURE-OTHER", "v1", book_id="experimental")            # no sign-off: BLOCKED
    admitted = admission.admit("FIXTURE-TREND", "v1", book_id="experimental", reviewer_run_id=signoff())
    insert = ("INSERT INTO office_deployments(deployment_id, strategy_id, version, book_id, risk_group, state, "
              "execution_kind, forward_protocol, admission_decision_id) "
              "VALUES (%s, %s, 'v1', 'experimental', %s, %s, 'NATIVE', '{}'::jsonb, %s)")
    for decision in (blocked["decision_id"], admitted["decision_id"]):                  # blocked, or another strategy's
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="its own ADMITTED decision"):
            sql(ready, insert, (str(uuid4()), "FIXTURE-OTHER", "FIXTURE-OTHER", "EXPERIMENTAL_PAPER", decision))
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(ready, insert, (str(uuid4()), "FIXTURE-TREND", "FIXTURE-TREND", "LIVE", admitted["decision_id"]))
    with pytest.raises(psycopg.errors.CheckViolation):                                   # ADMITTED needs a reviewer
        sql(ready, "INSERT INTO office_admission_decisions(decision_id, strategy_id, version, book_id, policy_version, "
                   "outcome, checks, input_sha256) VALUES (%s, 'FIXTURE-OTHER', 'v1', 'experimental', 'x', 'ADMITTED', "
                   "'[]'::jsonb, %s)", (str(uuid4()), "0" * 64))
    # a deployment's state may change (pause, retire); nothing else about it may, and it cannot be deleted
    sql(ready, "UPDATE office_deployments SET state = 'PAUSED'")
    for statement, message in (("UPDATE office_deployments SET book_id = 'experimental', version = 'v2'", "Only the state"),
                               ("UPDATE office_deployments SET execution_kind = 'EXTERNAL_LOCAL'", "Only the state"),
                               ("DELETE FROM office_deployments", "written once"),
                               ("UPDATE office_admission_decisions SET outcome = 'ADMITTED'", "written once"),
                               ("UPDATE office_books SET max_active_deployments = 20", "written once")):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match=message):
            sql(ready, statement)
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(ready, "UPDATE office_deployments SET state = 'LIVE'")
    with pytest.raises(roles.ToolNotAllowed), ready() as conn:
        tools.write_admission_decision("qa", conn, strategy_id="FIXTURE-OTHER", version="v1", book_id=None,
                                       policy_version="x", outcome="BLOCKED", checks=[], input_sha256="0" * 64,
                                       reviewer_run_id=None)
