"""Real PostgreSQL checks for QA scorecards, deployment state changes and learning proposals.

The strategy, its rule, its prices and its trades are test records in a disposable schema. Nothing here is a
real result, a broker order or a broker fill.
"""
from datetime import date
from uuid import uuid4

import psycopg
import pytest

from stock_machine.office import admission, inventory, paper, qa, research, status
from stock_machine.office.paper import Signal
from tests.test_office_admission_postgres import fixture_row, signoff
from tests.test_office_paper_postgres import COSTS, LIMITS, T, bar, day, desk  # noqa: F401
from tests.test_office_research_postgres import pg, sql  # noqa: F401

REGISTERED = {"min_trades": 2, "min_dates": 2, "pass_mean_net_bps": 10}
REFUSED = psycopg.errors.ObjectNotInPrerequisiteState


class Later(date):
    """The paper tests date their bars after today, so QA is told that a later day is today."""
    @classmethod
    def today(cls):
        return day(30)


@pytest.fixture
def floor(desk, monkeypatch):  # noqa: F811
    monkeypatch.setattr(qa, "date", Later)
    row = fixture_row("FIXTURE-TREND")
    row["forward_protocol"] = {**row["forward_protocol"], "evaluation": REGISTERED}
    inventory.load([row])
    admission.create_book("experimental", starting_equity=100000, max_active_deployments=2, limits=LIMITS,
                          cost_model=COSTS)
    result = admission.admit("FIXTURE-TREND", "v1", book_id="experimental", reviewer_run_id=signoff())
    assert result["outcome"] == "ADMITTED", result
    desk.deployment = result["deployment_id"]
    return desk


def first_trade(floor):
    """ACME: signal today, in at tomorrow's open (41), out at tomorrow's close (42). Net +114.625."""
    floor.signals[T] = [Signal("ACME", "LONG", 1)]
    paper.scan(floor.deployment, T)
    bar(floor.pg, "ACME", day(1), 41, 42)
    bar(floor.pg, "BETA", day(1), 100, 100)
    assert paper.fill_due("experimental")["filled"] == 1 and paper.close_due("experimental")["closed"] == 1
    paper.mark("experimental", day(1))


def second_trade(floor, close):
    """BETA: signal tomorrow, in at the next open (100), out at that close."""
    floor.signals[day(1)] = [Signal("BETA", "LONG", 1)]
    assert paper.scan(floor.deployment, day(1))["counts"] == {"ACCEPTED": 1}
    bar(floor.pg, "BETA", day(2), 100, close)
    assert paper.fill_due("experimental")["filled"] == 1 and paper.close_due("experimental")["closed"] == 1
    paper.mark("experimental", day(2))


def state(floor):
    return sql(floor.pg, "SELECT state FROM office_deployments WHERE deployment_id = %s", (floor.deployment,))[0][0]


def kinds(floor):
    return sql(floor.pg, "SELECT kind, as_of, maturity, verdict FROM office_evaluations ORDER BY as_of, kind")


def test_fills_reconcile_and_matured_pending_and_shadow_results_are_kept_apart(floor):
    first_trade(floor)
    early = qa.evaluate(floor.deployment, day(1))
    assert early["operational"]["verdict"] == "CONTINUE_COLLECTING" and early["operational"]["scorecard"]["defects"] == {}
    assert (early["strategy"]["maturity"], early["strategy"]["verdict"]) == ("PENDING", "CONTINUE_COLLECTING")
    assert early["strategy"]["scorecard"]["matured"]["net_usd"] == "114.62"
    second_trade(floor, 103)
    floor.signals[day(2)] = [Signal("NOSUCH", "LONG", 1)]                     # no price: rejected, a shadow signal
    paper.scan(floor.deployment, day(2))
    out = qa.evaluate(floor.deployment, day(2))
    card = out["strategy"]["scorecard"]
    ledger_net = sql(floor.pg, "SELECT sum(realized_pnl_usd), sum(costs_usd) FROM office_positions")[0]
    assert (card["matured"]["trades"], card["matured"]["decision_dates"]) == (2, 2)
    assert card["matured"]["net_usd"] == str(round(ledger_net[0], 2)) == "254.48"   # 114.625 + 139.85, half-even
    assert card["matured"]["costs_usd"] == str(round(ledger_net[1], 2))
    assert card["pending"] == {"open_positions": 0, "orders_waiting_for_a_fill": 0, "scored": False}
    assert card["shadow"]["signals_rejected"] == 1 and card["shadow"]["scored"] is False
    assert card["coverage"] == {"sessions_scanned": 3, "sessions_with_no_signal": 0, "sessions_with_a_signal": 3}
    assert out["operational"]["scorecard"]["defects"] == {}
    assert out["operational"]["scorecard"]["checked"]["mark_session"] == str(day(2))
    assert (out["strategy"]["maturity"], out["strategy"]["verdict"]) == ("MATURED", "REQUEST_QUALIFICATION_REVIEW")
    assert sql(floor.pg, "SELECT bool_and(simulated), count(*) FROM office_evaluations")[0] == (True, 4)
    # scoring changed nothing in the ledger, and a request for review changes no state
    assert admission.apply_evaluation(out["strategy"]["evaluation_id"]) == {"changed": False, "code": "NEEDS_OWNER_REVIEW"}
    assert state(floor) == "EXPERIMENTAL_PAPER" and floor.count("office_deployment_events") == 0


def test_repeating_an_evaluation_adds_nothing_and_the_registered_evaluation_runs_once(floor):
    first_trade(floor)
    second_trade(floor, 103)
    once = qa.evaluate(floor.deployment, day(2))
    again = qa.evaluate(floor.deployment, day(2))
    assert once["strategy"]["created"] and once["operational"]["created"]
    assert not again["operational"]["created"] and again["strategy"]["evaluation_id"] == once["strategy"]["evaluation_id"]
    later = qa.evaluate(floor.deployment, day(3))                              # a later look does not re-run it
    assert later["strategy"]["already_evaluated"] and later["strategy"]["as_of"] == str(day(2))
    assert later["operational"]["created"]
    assert kinds(floor) == [("OPERATIONAL", day(2), "NOT_APPLICABLE", "CONTINUE_COLLECTING"),
                            ("STRATEGY", day(2), "MATURED", "REQUEST_QUALIFICATION_REVIEW"),
                            ("OPERATIONAL", day(3), "NOT_APPLICABLE", "CONTINUE_COLLECTING")]
    row = "INSERT INTO office_evaluations(evaluation_id, deployment_id, kind, as_of, maturity, verdict, scorecard, " \
          "protocol, policy_version, input_sha256) VALUES (%s, %s, 'STRATEGY', %s, %s, %s, '{}', '{}', 'x', %s)"
    with pytest.raises(psycopg.errors.UniqueViolation):                         # a second matured result
        sql(floor.pg, row, (str(uuid4()), floor.deployment, day(9), "MATURED", "RETIRE_UNDER_REGISTERED_RULE", "0" * 64))
    for maturity, verdict in (("PENDING", "REQUEST_QUALIFICATION_REVIEW"), ("PENDING", "RETIRE_UNDER_REGISTERED_RULE"),
                              ("NOT_APPLICABLE", "CONTINUE_COLLECTING"), ("PENDING", "PAUSE_FOR_DATA_OR_EXECUTION_DEFECT"),
                              ("PENDING", "PROMOTE_TO_LIVE")):
        with pytest.raises(psycopg.errors.CheckViolation):                      # a pending result cannot conclude
            sql(floor.pg, row, (str(uuid4()), floor.deployment, day(9), maturity, verdict, "0" * 64))
    for statement in ("UPDATE office_evaluations SET verdict = 'CONTINUE_COLLECTING'", "DELETE FROM office_evaluations",
                      "TRUNCATE office_evaluations CASCADE"):
        with pytest.raises(REFUSED, match="written once"):
            sql(floor.pg, statement)
    with pytest.raises(ValueError, match="AS_OF_IN_THE_FUTURE"):
        qa.evaluate(floor.deployment, day(31))
    with pytest.raises(ValueError, match="DEPLOYMENT_NOT_FOUND"):
        qa.evaluate(str(uuid4()), day(2))


def test_a_bookkeeping_defect_pauses_the_deployment_and_only_the_owner_can_resume(floor):
    first_trade(floor)
    clean = qa.evaluate(floor.deployment, day(1))
    assert admission.apply_evaluation(clean["operational"]["evaluation_id"]) == {"changed": False, "code": "NO_CHANGE_NEEDED"}
    sql(floor.pg, "INSERT INTO office_marks(book_id, session, equity_usd, realized_usd, unrealized_usd, "
                  "gross_exposure_usd, open_positions) VALUES ('experimental', %s, 150000, 0, 0, 0, 0)", (day(2),))
    found = qa.evaluate(floor.deployment, day(2))["operational"]
    assert found["verdict"] == "PAUSE_FOR_DATA_OR_EXECUTION_DEFECT"
    assert found["scorecard"]["defects"] == {"MARK_DOES_NOT_RECONCILE": 1}
    assert admission.apply_evaluation(clean["operational"]["evaluation_id"]) == {"changed": False, "code": "NEWER_EVALUATION_EXISTS"}
    paused = admission.apply_evaluation(found["evaluation_id"])
    assert paused["changed"] and paused["state"] == "PAUSED" and state(floor) == "PAUSED"
    assert admission.apply_evaluation(found["evaluation_id"])["changed"] is False             # applying twice adds nothing
    assert sql(floor.pg, "SELECT from_state, to_state, actor, reason_code, evaluation_id::text, policy_version "
                         "FROM office_deployment_events") == [
        ("EXPERIMENTAL_PAPER", "PAUSED", "policy", "PAUSE_FOR_DATA_OR_EXECUTION_DEFECT", found["evaluation_id"],
         "deployment-state.v1")]
    # paused: no new entry, and the policy cannot resume it
    floor.signals[day(1)] = [Signal("BETA", "LONG", 1)]
    assert paper.scan(floor.deployment, day(1))["counts"] == {"REJECTED": 1}
    event = "INSERT INTO office_deployment_events(event_id, deployment_id, from_state, to_state, actor, " \
            "policy_version, reason_code, evaluation_id) VALUES (%s, %s, %s, %s, %s, 'x', 'A_REASON', %s)"
    with pytest.raises(REFUSED, match="does not support"):                                     # no verdict resumes
        sql(floor.pg, event, (str(uuid4()), floor.deployment, "PAUSED", "EXPERIMENTAL_PAPER", "policy", found["evaluation_id"]))
    with pytest.raises(psycopg.errors.CheckViolation):                                         # policy needs an evaluation
        sql(floor.pg, event, (str(uuid4()), floor.deployment, "PAUSED", "RETIRED", "policy", None))
    with pytest.raises(REFUSED, match="does not support"):                                     # a pause verdict cannot retire
        sql(floor.pg, event, (str(uuid4()), floor.deployment, "PAUSED", "RETIRED", "policy", found["evaluation_id"]))
    resumed = admission.set_state(floor.deployment, "EXPERIMENTAL_PAPER", owner="owner", reason_code="MARK_CORRECTED")
    assert resumed["changed"] and state(floor) == "EXPERIMENTAL_PAPER"
    assert sql(floor.pg, "SELECT actor FROM office_deployment_events ORDER BY created_at")[-1] == ("owner:owner",)


def test_a_failed_registered_evaluation_retires_the_deployment_for_good(floor):
    first_trade(floor)
    second_trade(floor, 97)                                                     # second trade loses more than the first made
    out = qa.evaluate(floor.deployment, day(2))["strategy"]
    assert (out["maturity"], out["verdict"]) == ("MATURED", "RETIRE_UNDER_REGISTERED_RULE")
    assert out["scorecard"]["matured"]["mean_net_bps_per_date"] < 10
    assert admission.apply_evaluation(out["evaluation_id"])["state"] == "RETIRED" and state(floor) == "RETIRED"
    with pytest.raises(ValueError, match="STATE_CHANGE_NOT_ALLOWED"):
        admission.set_state(floor.deployment, "EXPERIMENTAL_PAPER", owner="owner", reason_code="TRY_AGAIN")
    with pytest.raises(ValueError, match="DEPLOYMENT_NOT_SCANNABLE"):
        paper.scan(floor.deployment, day(2))
    assert admission.apply_evaluation(out["evaluation_id"])["changed"] is False
    # the losing trades are still there, and admitting the same version again returns the retired deployment
    assert floor.count("office_positions") == 2 and floor.count("office_fills") == 4
    again = admission.admit("FIXTURE-TREND", "v1", book_id="experimental", reviewer_run_id=signoff())
    assert again["created"] is False and again["state"] == "RETIRED"


def test_the_database_refuses_a_state_change_without_its_reason_or_to_any_other_state(floor):
    with pytest.raises(REFUSED, match="needs its written reason"):
        sql(floor.pg, "UPDATE office_deployments SET state = 'PAUSED'")
    with pytest.raises(REFUSED, match="needs its written reason"):
        sql(floor.pg, "UPDATE office_deployments SET state = 'QUALIFIED_PAPER'")
    event = "INSERT INTO office_deployment_events(event_id, deployment_id, from_state, to_state, actor, " \
            "policy_version, reason_code) VALUES (%s, %s, %s, %s, %s, 'x', 'A_REASON')"
    for to_state in ("QUALIFIED_PAPER", "LIVE", "BLOCKED"):
        with pytest.raises(REFUSED, match="not allowed"):
            sql(floor.pg, event, (str(uuid4()), floor.deployment, "EXPERIMENTAL_PAPER", to_state, "owner:owner"))
    with pytest.raises(psycopg.errors.CheckViolation):                           # an actor is the owner or the policy
        sql(floor.pg, event, (str(uuid4()), floor.deployment, "EXPERIMENTAL_PAPER", "PAUSED", "qa"))
    with pytest.raises(REFUSED, match="not allowed"):                            # not the state it is in
        sql(floor.pg, event, (str(uuid4()), floor.deployment, "PAUSED", "RETIRED", "owner:owner"))
    with pytest.raises(REFUSED, match="not allowed"):
        sql(floor.pg, event, (str(uuid4()), floor.deployment, "EXPERIMENTAL_PAPER", "EXPERIMENTAL_PAPER", "owner:owner"))
    # an event written in an earlier transaction does not cover a later change
    sql(floor.pg, event, (str(uuid4()), floor.deployment, "EXPERIMENTAL_PAPER", "PAUSED", "owner:owner"))
    with pytest.raises(REFUSED, match="needs its written reason"):
        sql(floor.pg, "UPDATE office_deployments SET state = 'PAUSED'")
    assert state(floor) == "EXPERIMENTAL_PAPER"
    assert admission.set_state(floor.deployment, "PAUSED", owner="owner", reason_code="OWNER_PAUSE")["changed"]
    assert admission.set_state(floor.deployment, "PAUSED", owner="owner", reason_code="OWNER_PAUSE")["changed"] is False
    for statement in ("UPDATE office_deployment_events SET actor = 'policy'", "DELETE FROM office_deployment_events",
                      "TRUNCATE office_deployment_events"):
        with pytest.raises(REFUSED, match="written once"):
            sql(floor.pg, statement)


def test_a_proposal_creates_a_new_version_and_leaves_the_original_and_its_history_alone(floor):
    first_trade(floor)
    scored = qa.evaluate(floor.deployment, day(1))["strategy"]
    before = sql(floor.pg, "SELECT record_sha256, admissible, original_status FROM office_strategy_versions "
                           "WHERE strategy_id = 'FIXTURE-TREND' AND version = 'v1'")
    ledger_before = [floor.count(t) for t in ("office_opportunities", "office_orders", "office_fills", "office_positions",
                                              "office_marks", "office_evaluations")]
    idea = dict(reason="Hold two sessions instead of one, to be tested from scratch.", change={"holding_sessions": 2},
                evaluation_id=scored["evaluation_id"])
    made = qa.propose("FIXTURE-TREND", "v1", "v2-idea", **idea)
    assert made["created"] and made["needs"] == ["FROZEN_RULES", "DEVELOPMENT_RESULT", "NEW_FORWARD_PROTOCOL",
                                                 "NEW_ADMISSION_DECISION"]
    assert qa.propose("FIXTURE-TREND", "v1", "v2-idea", **idea)["created"] is False
    with pytest.raises(ValueError, match="PROPOSED_VERSION_ALREADY_USED"):
        qa.propose("FIXTURE-TREND", "v1", "v2-idea", **{**idea, "change": {"holding_sessions": 5}})
    for bad in (("FIXTURE-TREND", "v1", "v1"), ("FIXTURE-TREND", "v1", "has space")):
        with pytest.raises(ValueError, match="PROPOSAL_INVALID"):
            qa.propose(*bad, **idea)
    with pytest.raises(ValueError, match="STRATEGY_NOT_IN_INVENTORY"):
        qa.propose("NO-SUCH", "v1", "v2", **idea)
    assert status.summary()["qa"]["proposals_waiting"] == 1
    # nothing exists under the new version until the owner accepts the proposal
    assert sql(floor.pg, "SELECT count(*) FROM office_strategy_versions WHERE strategy_id = 'FIXTURE-TREND'")[0][0] == 1
    accepted = qa.accept_proposal(made["proposal_id"], accepted_by="owner")
    assert accepted["status"] == "CREATED" and accepted["admissible"] is False and accepted["version"] == "v2-idea"
    assert qa.accept_proposal(made["proposal_id"], accepted_by="owner")["status"] == "PRESENT"
    new = sql(floor.pg, "SELECT admissible, execution_kind, evidence_tier, original_status, record->'hashes', "
                        "record->'proposal'->>'from_version' FROM office_strategy_versions WHERE version = 'v2-idea'")
    assert new == [(False, "NONE", "NONE", "Proposed from v1; no evidence", {}, "v1")]
    # the original version, its deployment and its whole paper history are exactly as they were
    assert sql(floor.pg, "SELECT record_sha256, admissible, original_status FROM office_strategy_versions "
                         "WHERE strategy_id = 'FIXTURE-TREND' AND version = 'v1'") == before
    assert [floor.count(t) for t in ("office_opportunities", "office_orders", "office_fills", "office_positions",
                                     "office_marks", "office_evaluations")] == ledger_before
    assert state(floor) == "EXPERIMENTAL_PAPER" and floor.count("office_deployments") == 1
    # an idea is not a result: it cannot be admitted to the paper book
    blocked = admission.admit("FIXTURE-TREND", "v2-idea", book_id="experimental", reviewer_run_id=signoff("FIXTURE-TREND", "v2-idea"))
    assert blocked["outcome"] == "BLOCKED" and "ORIGINAL_STATUS_NOT_ADMISSIBLE" in blocked["checks"][0]["codes"]
    assert status.summary()["qa"]["proposals_waiting"] == 0
    with pytest.raises(ValueError, match="PROPOSAL_NOT_FOUND"):
        qa.accept_proposal(str(uuid4()), accepted_by="owner")
    with pytest.raises(REFUSED, match="must name a new version"):               # the database refuses a reused version
        sql(floor.pg, "INSERT INTO office_proposals(proposal_id, strategy_id, version, proposed_version, reason, change) "
                      "VALUES (%s, 'FIXTURE-TREND', 'v2-idea', 'v1', 'a reason long enough', '{}')", (str(uuid4()),))
    for statement in ("UPDATE office_proposals SET reason = 'a different reason'", "DELETE FROM office_proposals",
                      "TRUNCATE office_proposals"):
        with pytest.raises(REFUSED, match="written once"):
            sql(floor.pg, statement)


def test_the_status_page_and_the_weekly_briefing_show_the_latest_assessments(floor):
    assert status.summary()["qa"] == {"evaluations": [], "proposals_waiting": 0}
    assert "paper_qa" not in research.build_briefing("WEEKLY", day(-7))["content"]
    first_trade(floor)
    qa.evaluate(floor.deployment, day(1))
    shown = status.summary()["qa"]["evaluations"]
    assert [(e["kind"], e["as_of"], e["verdict"], e["simulated"]) for e in shown] == [
        ("OPERATIONAL", str(day(1)), "CONTINUE_COLLECTING", True), ("STRATEGY", str(day(1)), "CONTINUE_COLLECTING", True)]
    assert shown[0]["defects"] == {} and (shown[1]["trades"], shown[1]["decision_dates"], shown[1]["maturity"]) == (1, 1, "PENDING")
    briefing = research.build_briefing("WEEKLY")["content"]
    assert briefing["paper_qa"]["evaluations"][1]["net_usd"] == "114.62"
